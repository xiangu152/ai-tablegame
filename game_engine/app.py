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
from pathlib import Path
from typing import Optional

from agentscope.agent import Agent
from agentscope.state import AgentState
from agentscope.model import DeepSeekChatModel
from agentscope.credential import DeepSeekCredential
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
)
from .middleware import GameLoggingMiddleware, GameContextCompressor, MemoryInjectorMiddleware, GamePhaseMiddleware
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
    """

    def __init__(self):
        self._room_events: dict[str, asyncio.Event] = {}
        self._agent_events: dict[str, asyncio.Event] = {}

    def notify(self, room: str):
        if room in self._room_events:
            self._room_events[room].set()

    def notify_agent(self, agent_name: str):
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


# ============================================================
# Game Manager
# ============================================================

class GameManager:
    """Manages a single DND game session with AgentScope agents."""

    def __init__(self, game_name: str):
        self.game_name = game_name
        game_dir = Path("dm_memory") / game_name

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

    def _build_middlewares(self, memory_dir: str) -> list:
        return [
            GameLoggingMiddleware(game_name=self.game_name),
            GameContextCompressor(trigger_ratio=0.8, reserve_ratio=0.15),
            MemoryInjectorMiddleware(memory_dir=memory_dir, max_summary_chars=2000),
        ]

    def _get_lock(self, agent_name: str) -> asyncio.Lock:
        if agent_name not in self._agent_locks:
            self._agent_locks[agent_name] = asyncio.Lock()
        return self._agent_locks[agent_name]

    # ============================================================
    # Agent Creation
    # ============================================================

    def create_dm_agent(self) -> Agent:
        dm_mem = self._create_memory_dir("DM")

        system_prompt = _read_role_file("DM.md")
        system_prompt += f"""

## Current Game: {self.game_name}

Files:
  dm_memory/{self.game_name}/adventure_text.json — the adventure module
  dm_memory/{self.game_name}/players/ — character cards
  dm_memory/{self.game_name}/game_memory.json — game state

Important: The human specified {self.expected_player_count} players. Create exactly this many now.

## Your Memory: {dm_mem}
Private workspace for session notes, NPC tracking, plot ideas.

## Tools You Have
- **Chat**: send/listen in 酒馆大厅 (public), DM-思考室 (private thinking)
  Use action=create_room to create DM↔PL 1v1 rooms
- **CreatePlayer**: spawn new Player agents (at startup or replacement)
- **Card**: create/manage character cards
- **Dice**: all dice rolls
- **Read**: adventure text, rulebooks, YOUR memory only
- **Write**: save to YOUR memory, game files
- **GameState**: save/load game state

## Rules
- ❌ NEVER read player memory files
- ❌ NEVER use templated speech like "tank/healer/dps/utility"
- ❌ NEVER suggest what classes players should pick
- You are the cold, impartial world. Let players struggle.
- Post thinking to 'DM-思考室', narrate scenes to '酒馆大厅'
- 🌐 ALL communication MUST be in Chinese (中文)
- 🎲 Players speak IN TURN: address them one at a time in order
- 🎲 If a player says "(pass)", accept it and move to the next player
"""

        model = DeepSeekChatModel(
            credential=DeepSeekCredential(
                api_key=os.environ.get("DEEPSEEK_API_KEY", "")),
            model="deepseek-chat", stream=True,
        )

        from agentscope.tool._builtin import Bash, Grep, Glob
        toolkit = Toolkit(tools=[
            ChatTool(chat_room=self.chat, agent_name="DM",
                     notifier=self.notifier),
            CreatePlayerTool(game_manager=self, agent_name="DM"),
            DiceTool(dice=self.dice),
            CardTool(player_card=self.cards, agent_name="DM"),
            GameReadTool(agent_name="DM", memory_dir=str(dm_mem)),
            GameWriteTool(agent_name="DM", memory_dir=str(dm_mem)),
            GameStateTool(game_session=None, agent_name="DM"),
            Bash(), Grep(), Glob(),
        ])

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

        self.dm_agent = Agent(
            name="DM", system_prompt=system_prompt, model=model,
            toolkit=toolkit, middlewares=self._build_middlewares(str(dm_mem)) + [GamePhaseMiddleware(game_name=self.game_name, player_count=self.expected_player_count)],
            state=state,
        )
        self.chat.register("DM", role="dm")
        return self.dm_agent

    def create_player_agent(self, player_name: str) -> Agent:
        pl_mem = self._create_memory_dir(player_name)

        system_prompt = _read_role_file("PLAYER.md")
        system_prompt += f"""

## Your Identity
You are '{player_name}' in '{self.game_name}'.
Character card: dm_memory/{self.game_name}/players/{player_name}.json

## Your Memory: {pl_mem}
Private journal. Use Read/Write to track notes, clues, suspicions, goals.

## Turn-Based Play Rules
- 🎲 Players speak IN TURN order: player1 → player2 → player3 → player4
- 🎲 ONLY speak when it's YOUR turn. Wait for the DM to address you.
- 🎲 If you have nothing to say this turn, send: Chat(action="send", room="酒馆大厅", content="(pass)")
- 🎲 "(pass)" means you skip your turn — the DM will move to the next player.
- 🎲 After speaking OR passing, call WaitForMessages(room='酒馆大厅').

## What You Can Do
- ✅ Chat: send/listen in 酒馆大厅 (public)
- ✅ Dice: roll for attacks, saves, checks
- ✅ Card: view your character sheet (action=get_summary)
- ✅ Read: rulebooks, your card, your memory files
- ✅ Write: your memory directory only
- ✅ WaitForMessages: pause until new messages arrive
- 🌐 ALL roleplay, dialogue, and actions MUST be in Chinese (中文)

## What You CANNOT Do
- ❌ Read adventure_text, game_memory — DM-only (will be blocked)
- ❌ Read other players' cards or memory
- ❌ Access DM-思考室
- ❌ Create players, save/load game state

## How To Play
1. Read chat: Chat(action="listen", room="酒馆大厅")
2. Check yourself: Card(action="get_summary", name="{player_name}")
3. Review journal: Read(file_path="{pl_mem}/journal.md")
4. Act: Chat(action="send", room="酒馆大厅", content="your action/dialogue")
5. Take notes: Write(file_path="{pl_mem}/journal.md", content="...")
6. Yield: WaitForMessages(room="酒馆大厅")
"""

        model = DeepSeekChatModel(
            credential=DeepSeekCredential(
                api_key=os.environ.get("DEEPSEEK_API_KEY", "")),
            model="deepseek-chat", stream=True,
        )

        toolkit = Toolkit(tools=[
            ChatTool(chat_room=self.chat, agent_name=player_name,
                     notifier=self.notifier),
            DiceTool(dice=self.dice),
            CardTool(player_card=self.cards, agent_name=player_name),
            GameReadTool(agent_name=player_name, memory_dir=str(pl_mem)),
            GameWriteTool(agent_name=player_name, memory_dir=str(pl_mem)),
            WaitForMessages(),
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
            name=player_name, system_prompt=system_prompt, model=model,
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
        """DM turn: observe chat, respond, then wait for players."""
        if not self.dm_agent or not self._running:
            return

        async with self._get_lock("DM"):
            from agentscope.message import UserMsg
            prompt = (
                f"Game '{self.game_name}' is active. "
                f"Read chat: Chat(action=listen, room='酒馆大厅'). "
                f"Read your memory notes. "
                f"If it's your first turn: create player agents using CreatePlayer "
                f"(human told you how many), then announce the opening scene. "
                f"If mid-game: respond to player actions, advance the plot. "
                f"Post thinking to 'DM-思考室'. Narrate to '酒馆大厅'. "
                f"After narrating, call WaitForMessages(room='酒馆大厅') "
                f"to yield and wait for player responses."
            )
            reply = await self.dm_agent.reply(UserMsg(name="system", content=prompt))
            return reply.get_text_content() or ""

    # ── Player Turn ──

    async def player_observe_and_reply(self, player_name: str):
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
            from agentscope.message import UserMsg
            prompt = (
                f"Game '{self.game_name}'. You are '{player_name}'. "
                f"Read chat: Chat(action=listen, room='酒馆大厅'). "
                f"Check yourself: Card(action=get_summary, name='{player_name}'). "
                f"Review journal. "
                f"Respond in character — actions and dialogue. "
                f"Save notes to journal. "
                f"After acting, call WaitForMessages(room='酒馆大厅') "
                f"to yield your turn."
            )
            reply = await agent.reply(UserMsg(name="system", content=prompt))
            return reply.get_text_content() or ""

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


def create_game_manager(game_name: str) -> GameManager:
    if game_name in _game_managers:
        return _game_managers[game_name]
    manager = GameManager(game_name=game_name)
    _game_managers[game_name] = manager
    return manager


def remove_game_manager(game_name: str):
    manager = _game_managers.pop(game_name, None)
    if manager:
        asyncio.create_task(manager.stop_game())


def get_game_manager(game_name: str) -> Optional[GameManager]:
    return _game_managers.get(game_name)
