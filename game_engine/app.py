"""
Game App — AgentScope-powered DND game manager.

Design:
- Three chat room types: public (酒馆大厅), DM↔PL (1v1), PL↔PL (DM-authorized)
- Event-driven notification with per-room + per-agent target signals
- DM: full tools, CANNOT read player memory dirs, don't help PL
- Player: Chat, Dice, Card, Read(rulebooks+own card+own memory), Write(own memory), Wait
- DM creates PL agents at startup via CreatePlayer tool
- Singleton processing: each agent handles one message at a time
- Death: HP=0 + failed saves → removed from game loop
- All game decisions and plot advancement via chat text
"""

import os
import json
import asyncio
import logging
import yaml
from pathlib import Path
from typing import Optional

from agentscope.agent import Agent
from agentscope.state import AgentState
from agentscope.model import AnthropicChatModel
from agentscope.credential import AnthropicCredential
from agentscope.tool import Toolkit
from agentscope.permission import (
    PermissionContext,
    PermissionMode,
    PermissionRule,
    PermissionBehavior,
)

from .tools import (
    ChatTool,
    DiceTool,
    CardTool,
    GameReadTool,
    GameWriteTool,
    GameStateTool,
    WaitForMessages,
    CreatePlayerTool,
    SignalTool,
)
from .middleware import (
    GameLoggingMiddleware, GameContextCompressor,
    MemoryInjectorMiddleware, GamePhaseMiddleware,
    ChatContextMiddleware, ToolCallCompactor,
)
from .chat_room import ChatRoom
from .player_card import PlayerCard
from .dice import Dice
from .checkpoint import CheckpointManager

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_role_file(filename: str) -> str:
    path = PROJECT_ROOT / "agent_roles" / filename
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ============================================================
# Message Notifier — event-driven wake (OS interrupt style)
# ============================================================

class MessageNotifier:
    """Two-level notification: room broadcast + per-agent target.

    Room-level: notify(room) → wakes ALL agents waiting on that room
    Agent-level: notify_agent(agent_name) → wakes a specific agent
    Combined: wait_room_or_signal(room, agent) → wakes on either, returns reason
    """

    def __init__(self):
        self._room_events: dict[str, asyncio.Event] = {}
        self._agent_events: dict[str, asyncio.Event] = {}
        self._agent_signal_reasons: dict[str, str] = {}

    def notify(self, room: str):
        if room in self._room_events:
            self._room_events[room].set()

    def notify_agent(self, agent_name: str, reason: str = "signal"):
        """Wake a specific agent with an optional reason (e.g. 'dm_turn', 'your_turn')."""
        self._agent_signal_reasons[agent_name] = reason
        if agent_name in self._agent_events:
            self._agent_events[agent_name].set()

    async def wait_room(self, room: str) -> None:
        if room not in self._room_events:
            self._room_events[room] = asyncio.Event()
        self._room_events[room].clear()
        await self._room_events[room].wait()

    async def wait_agent(self, agent_name: str) -> None:
        if agent_name not in self._agent_events:
            self._agent_events[agent_name] = asyncio.Event()
        self._agent_events[agent_name].clear()
        await self._agent_events[agent_name].wait()

    async def wait_room_or_signal(
        self, room: str, agent_name: str, timeout: float = 120.0
    ) -> tuple[bool, str]:
        """Wait for either room messages OR explicit agent signal.

        Returns (was_signaled, reason).
        was_signaled=True means the agent was explicitly targeted.
        reason describes what happened (e.g. 'your_turn', 'room_message', 'timeout').
        """
        # Set up fresh events
        room_evt = asyncio.Event()
        agent_evt = asyncio.Event()
        self._room_events[room] = room_evt
        self._agent_events[agent_name] = agent_evt

        done_reason = {"value": "timeout"}
        combined = asyncio.Event()

        async def on_room():
            await room_evt.wait()
            done_reason["value"] = "room_message"
            combined.set()

        async def on_signal():
            await agent_evt.wait()
            reason = self._agent_signal_reasons.get(agent_name, "signal")
            done_reason["value"] = reason
            combined.set()

        t1 = asyncio.create_task(on_room())
        t2 = asyncio.create_task(on_signal())

        try:
            await asyncio.wait_for(combined.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            done_reason["value"] = "timeout"
        finally:
            t1.cancel()
            t2.cancel()

        was_signaled = done_reason["value"] not in ("room_message", "timeout")
        return was_signaled, done_reason["value"]


# ============================================================
# Game Manager
# ============================================================

class GameManager:
    """Manages a single DND game session with AgentScope agents."""

    def __init__(self, game_name: str, config_path: str = "config.yaml"):
        self.game_name = game_name
        game_dir = Path("dm_memory") / game_name

        # Load config & create shared model
        config = self._load_config(config_path)
        api_cfg = config.get("api", {})
        self.model = AnthropicChatModel(
            credential=AnthropicCredential(
                api_key=api_cfg.get("api_key") or os.environ.get("API_KEY", ""),
                base_url=api_cfg.get("base_url", ""),
            ),
            model=api_cfg.get("model", "mimo-v2.5"),
            stream=True,
        )

        self.chat = ChatRoom(game_name)
        self.cards = PlayerCard(game_name)
        self.dice = Dice()
        self.notifier = MessageNotifier()
        self._memory_base = game_dir / "memory"

        self.dm_agent: Optional[Agent] = None
        self.player_agents: dict[str, Agent] = {}
        self.checkpoint = CheckpointManager(game_name)

        # Singleton locks: one per agent, ensures single-threaded processing
        self._agent_locks: dict[str, asyncio.Lock] = {}

        # Dead agents (removed from game loop)
        self._dead_players: set[str] = set()

        # Room message cursors: room → last delivered msg id
        self._room_cursors: dict[str, int] = {}

        self._running = False
        self.expected_player_count = 0  # set by game loop
        self._agents_busy = 0  # heartbeat: skip when > 0

        # Event bus for streaming speech (asyncio.Queue)
        self._event_bus: asyncio.Queue = asyncio.Queue()

    def publish_event(self, event: dict):
        """Publish an event to the SSE stream (non-blocking)."""
        try:
            self._event_bus.put_nowait(event)
        except asyncio.QueueFull:
            pass

    async def stream_events(self):
        """Async generator for SSE event stream."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_bus.get(), timeout=1.0)
                yield event
            except asyncio.TimeoutError:
                pass

    def signal_player(self, target: str) -> dict:
        """Signal a specific player to take their turn. 'all' wakes everyone."""
        if target == "all":
            self.notifier.notify("酒馆大厅")
            for name in self.player_agents:
                self.notifier.notify_agent(name, reason="your_turn")
            return {"status": "ok", "target": "all", "woke": len(self.player_agents)}
        elif target == "DM":
            self.notifier.notify_agent("DM", reason="your_turn")
            return {"status": "ok", "target": "DM", "woke": 1}
        elif target in self.player_agents:
            self.notifier.notify_agent(target, reason="your_turn")
            return {"status": "ok", "target": target, "woke": 1}
        else:
            return {"status": "error", "message": f"Unknown target: {target}"}

    # ============================================================
    # Agent Memory
    # ============================================================

    def _create_memory_dir(self, agent_name: str) -> Path:
        mem_dir = self._memory_base / agent_name
        mem_dir.mkdir(parents=True, exist_ok=True)
        readme = mem_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                f"# {agent_name}'s Memory\n\n"
                f"Private workspace. Use Read/Write to manage notes.\n",
                encoding="utf-8",
            )
        return mem_dir

    def _build_middlewares(self, memory_dir: str, is_dm: bool = False) -> list:
        mws = [
            GameLoggingMiddleware(game_name=self.game_name),
            GameContextCompressor(trigger_ratio=0.8, reserve_ratio=0.15),
            MemoryInjectorMiddleware(memory_dir=memory_dir, max_summary_chars=2000),
            ChatContextMiddleware(game_name=self.game_name, room="酒馆大厅",
                                  limit=30, is_dm=is_dm),
            ToolCallCompactor(),
        ]
        if is_dm:
            mws.append(GamePhaseMiddleware(
                game_name=self.game_name,
                player_count=self.expected_player_count,
            ))
        return mws

    @staticmethod
    def _load_config(path: str) -> dict:
        p = Path(path)
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _get_lock(self, agent_name: str) -> asyncio.Lock:
        if agent_name not in self._agent_locks:
            self._agent_locks[agent_name] = asyncio.Lock()
        return self._agent_locks[agent_name]

    # ============================================================
    # Agent Creation
    # ============================================================

    def create_dm_agent(self, state=None) -> Agent:
        dm_mem = self._create_memory_dir("DM")

        system_prompt = _read_role_file("DM.md")
        system_prompt += f"""

Current Game: {self.game_name}
Players expected: {self.expected_player_count}

Files:
  dm_memory/{self.game_name}/adventure_text.json — the adventure module (full text)
  dm_memory/{self.game_name}/players/ — character cards
  dm_memory/{self.game_name}/game_memory.json — game state

DM Guides (read these BEFORE describing scenes):
  dnd_data/game_book/dm_guide_cos.md — overview, chapter structure, level table
  dnd_data/game_book/cos_scenes.md — scene details, NPCs, combat, plot branches
  dnd_data/game_book/cos_npcs.md — all NPC stats and dialogue
  dnd_data/game_book/cos_full_guide.md — full plot, items, endings

Always combine guides with adventure_text.json to verify scenes are accurate.

Game Flow (follow this order)
1. **Create Players**: Use CreatePlayer tool to create all {self.expected_player_count} players.
2. **Guide Character Creation**: Ask each player ONE BY ONE about their character concept (race, class, background). After each player describes their character, use Card(action="create") to create their character card. Address players by name: "player1, 你是什么人？"
3. **Opening Scene**: After all cards are created, narrate a brief opening scene to 酒馆大厅.
4. **Advance Plot**: Describe the world, present challenges, react to player actions. You drive the story forward — don't let players chat aimlessly. If players are idle, introduce an NPC, describe an event, or ask "你们打算怎么做？"

Rules
- ALL communication MUST be in Chinese (中文)
- NEVER read player memory files
- NEVER use templated speech like "tank/healer/dps/utility"
- NEVER suggest what classes players should pick
- You are the cold, impartial world. Let players struggle.
- Post thinking to 'DM-思考室', narrate scenes to '酒馆大厅'
- Address players by name when speaking to them
- If a player says "(pass)", accept it and move to the next player
- After narrating, call WaitForMessages(room='酒馆大厅') to yield your turn.
- After creating a player with CreatePlayer, use Signal(target=player_name) to wake them.
"""

        from agentscope.tool._builtin import Bash, Grep, Glob
        toolkit = Toolkit(tools=[
            ChatTool(chat_room=self.chat, agent_name="DM",
                     notifier=self.notifier),
            CreatePlayerTool(game_manager=self, agent_name="DM"),
            SignalTool(game_manager=self, agent_name="DM"),
            DiceTool(dice=self.dice),
            CardTool(player_card=self.cards, agent_name="DM"),
            GameReadTool(agent_name="DM", memory_dir=str(dm_mem)),
            GameWriteTool(agent_name="DM", memory_dir=str(dm_mem)),
            GameStateTool(game_session=None, agent_name="DM"),
            WaitForMessages(notifier=self.notifier, chat=self.chat,
                            agent_name="DM"),
            Bash(), Grep(), Glob(),
        ])

        if state is None:
            state = AgentState()
        state.permission_context = PermissionContext(
            mode=PermissionMode.BYPASS,
            deny_rules={
                "Read": [
                    PermissionRule(
                        tool_name="Read",
                        rule_content=f"*/memory/*",
                        behavior=PermissionBehavior.DENY, source="game"),
                    PermissionRule(
                        tool_name="Read",
                        rule_content=f"!*/memory/DM/*",
                        behavior=PermissionBehavior.ALLOW, source="game"),
                ],
            },
        )

        from agentscope.agent._config import ReActConfig
        react_config = ReActConfig(data={"max_iters": 50})

        self.dm_agent = Agent(
            name="DM", system_prompt=system_prompt, model=self.model,
            toolkit=toolkit, middlewares=self._build_middlewares(str(dm_mem), is_dm=True),
            state=state, react_config=react_config,
        )
        self.chat.register("DM", role="dm")
        return self.dm_agent

    def create_player_agent(self, player_name: str) -> Agent:
        pl_mem = self._create_memory_dir(player_name)

        system_prompt = _read_role_file("PLAYER.md")
        system_prompt += f"""

Your Identity
You are '{player_name}' in '{self.game_name}'.
Character card: dm_memory/{self.game_name}/players/{player_name}.json
Private journal: {pl_mem}/journal.md

Rules
- ALL roleplay, dialogue, and actions MUST be in Chinese (中文)
- When you wake up, check if the DM is addressing YOU. If yes, respond.
- If the message is NOT directed at you, call WaitForMessages again.
- ONLY speak when addressed by DM or when it's clearly your turn.
- If you have nothing to say: Chat(action="send", room="酒馆大厅", content="(pass)")
- After speaking OR passing, call WaitForMessages(room='酒馆大厅') to yield.
- Use Card(action="get_summary", name="{player_name}") to check your sheet.
- Use Read/Write to manage your journal notes.
- NEVER read adventure_text, game_memory — DM-only (will be blocked)
- NEVER read other players' cards or memory
- NEVER access DM-思考室
- NEVER play as NPC or other characters — you can only play yourself
- NEVER invent plot, NPCs, or locations — all described by DM
"""

        from agentscope.tool._builtin import Grep
        toolkit = Toolkit(tools=[
            ChatTool(chat_room=self.chat, agent_name=player_name,
                     notifier=self.notifier),
            DiceTool(dice=self.dice),
            CardTool(player_card=self.cards, agent_name=player_name),
            GameReadTool(agent_name=player_name, memory_dir=str(pl_mem)),
            GameWriteTool(agent_name=player_name, memory_dir=str(pl_mem)),
            WaitForMessages(notifier=self.notifier, chat=self.chat,
                            agent_name=player_name),
            Grep(),
        ])

        state = AgentState()
        state.permission_context = PermissionContext(
            mode=PermissionMode.DEFAULT,
            deny_rules={
                "Read": [
                    PermissionRule(tool_name="Read", rule_content="*adventure_text*",
                                   behavior=PermissionBehavior.DENY, source="game"),
                    PermissionRule(tool_name="Read", rule_content="*adventure_chapters*",
                                   behavior=PermissionBehavior.DENY, source="game"),
                    PermissionRule(tool_name="Read", rule_content="*game_memory*",
                                   behavior=PermissionBehavior.DENY, source="game"),
                    PermissionRule(tool_name="Read", rule_content="*rulebook_index*",
                                   behavior=PermissionBehavior.DENY, source="game"),
                    PermissionRule(tool_name="Read", rule_content="*/players/*",
                                   behavior=PermissionBehavior.DENY, source="game"),
                    PermissionRule(tool_name="Read", rule_content="*/memory/*",
                                   behavior=PermissionBehavior.DENY, source="game"),
                    PermissionRule(tool_name="Read", rule_content=f"!*/players/{player_name}.json",
                                   behavior=PermissionBehavior.ALLOW, source="game"),
                    PermissionRule(tool_name="Read", rule_content=f"!*/memory/{player_name}/*",
                                   behavior=PermissionBehavior.ALLOW, source="game"),
                ],
                "Write": [
                    PermissionRule(tool_name="Write", rule_content="*game_memory*",
                                   behavior=PermissionBehavior.DENY, source="game"),
                    PermissionRule(tool_name="Write", rule_content="*/players/*",
                                   behavior=PermissionBehavior.DENY, source="game"),
                    PermissionRule(tool_name="Write", rule_content="*/memory/*",
                                   behavior=PermissionBehavior.DENY, source="game"),
                    PermissionRule(tool_name="Write", rule_content=f"!*/memory/{player_name}/*",
                                   behavior=PermissionBehavior.ALLOW, source="game"),
                ],
                "GameState": [
                    PermissionRule(tool_name="GameState", rule_content=None,
                                   behavior=PermissionBehavior.DENY, source="game"),
                ],
            },
        )

        agent = Agent(
            name=player_name, system_prompt=system_prompt, model=self.model,
            toolkit=toolkit, middlewares=self._build_middlewares(str(pl_mem)),
            state=state,
        )
        self.chat.register(player_name, role="player")
        self.player_agents[player_name] = agent
        return agent

    # ============================================================
    # Game Loop
    # ============================================================

    async def start_game(self):
        self._running = True
        logger.info("[%s] Game started!", self.game_name)

    async def stop_game(self):
        self._running = False
        self.expected_player_count = 0  # set by game loop
        logger.info("[%s] Game stopped.", self.game_name)

    def kill_player(self, player_name: str):
        """Mark a player as dead — removed from game loop."""
        self._dead_players.add(player_name)
        room_id = self._get_room_id("酒馆大厅")
        if room_id:
            self.chat.send_system(
                room_id,
                f"💀 {player_name} has fallen. They will not rise again.",
            )
        logger.info("[%s] %s is DEAD. Removed from game.", self.game_name, player_name)

    def _get_room_id(self, room_name: str) -> str | None:
        rooms = self.chat.list_public_rooms()
        for r in rooms:
            if r["name"] == room_name:
                return r["id"]
        return None

    def _check_death(self, player_name: str) -> bool:
        """Check if a player's character is dead (HP=0 + failed saves)."""
        try:
            card = self.cards.get(player_name)
            combat = card.get("combat", {})
            hp = combat.get("hp_current", 1)
            death_saves = combat.get("death_saves", {})
            if hp <= 0 and death_saves.get("failures", 0) >= 3:
                return True
        except Exception:
            pass
        return False

    # ── DM Turn ──


    async def dm_observe_and_reply(self):
        """DM turn: observe chat, respond, then wait."""
        if not self.dm_agent or not self._running:
            return

        async with self._get_lock("DM"):
            self._agents_busy += 1
            try:
                return await self._dm_reply_impl()
            finally:
                self._agents_busy -= 1

    async def _dm_reply_impl(self):
        from agentscope.message import UserMsg

        # Detect phase from GamePhaseMiddleware
        phase_mw = None
        for mw in self.dm_agent._system_prompt_middlewares:
            if isinstance(mw, GamePhaseMiddleware):
                phase_mw = mw
                break

        existing = len(self.player_agents)
        needed = self.expected_player_count

        if existing < needed:
            prompt = (
                f"[DM Turn] Create {needed - existing} players with CreatePlayer. "
                f"Then Signal(all). Act then WaitForMessages."
            )
        elif phase_mw and phase_mw._detect_phase()[0] == "character_creation":
            created = phase_mw._get_created_players()
            prompt = (
                f"[DM Turn] Character creation: {len(created)}/{needed} cards. "
                f"Ask next player their character concept, then Card(create). "
                f"Act then WaitForMessages."
            )
        else:
            prompt = (
                f"[DM Turn] All {needed} cards created. "
                f"Advance the plot. Act then WaitForMessages."
            )
        reply = await self.dm_agent.reply(UserMsg(name="system", content=prompt))
        return reply.get_text_content() or ""

    # ── Player Turn ──

    async def player_observe_and_reply(self, player_name: str):
        """Player turn: triggered by signal or room messages."""
        agent = self.player_agents.get(player_name)
        if not agent or not self._running:
            return
        if player_name in self._dead_players:
            return

        # Check death before turn
        if self._check_death(player_name):
            self.kill_player(player_name)
            return

        async with self._get_lock(player_name):
            self._agents_busy += 1
            try:
                from agentscope.message import UserMsg
                prompt = f"[Turn] {player_name}. Act then WaitForMessages."
                reply = await agent.reply(UserMsg(name="system", content=prompt))
                return reply.get_text_content() or ""
            finally:
                self._agents_busy -= 1

    # ── External Wait Handler ──



    async def run_game_round(self, player_names: list[str]):
        results = []
        await self.dm_observe_and_reply()
        for pname in player_names:
            if pname not in self._dead_players:
                await self.player_observe_and_reply(pname)
        return results

    def status(self) -> dict:
        return {
            "game": self.game_name, "running": self._running,
            "dm_ready": self.dm_agent is not None,
            "players": [p for p in self.player_agents if p not in self._dead_players],
            "dead": list(self._dead_players),
        }


# ============================================================
# Global Registry
# ============================================================

_game_managers: dict[str, GameManager] = {}


def create_game_manager(game_name: str, config_path: str = "config.yaml") -> GameManager:
    if game_name in _game_managers:
        return _game_managers[game_name]
    manager = GameManager(game_name=game_name, config_path=config_path)
    _game_managers[game_name] = manager
    return manager


def remove_game_manager(game_name: str):
    manager = _game_managers.pop(game_name, None)
    if manager:
        asyncio.create_task(manager.stop_game())


def get_game_manager(game_name: str) -> Optional[GameManager]:
    return _game_managers.get(game_name)
