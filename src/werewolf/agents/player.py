"""Player agent - all agents see full public info, debate context, and tool-access to private data."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import jinja2

from werewolf.agents.base import AgentResponse, BaseAgent
from werewolf.config import GameConfig
from werewolf.engine.state import Camp, GameState, Role

logger = logging.getLogger(__name__)

ROLE_CHINESE: dict[Role, str] = {
    Role.WEREWOLF: "狼人", Role.SEER: "预言家", Role.WITCH: "女巫",
    Role.HUNTER: "猎人", Role.GUARD: "守卫", Role.VILLAGER: "平民",
}

ROLE_TEMPLATE: dict[Role, str] = {
    Role.WEREWOLF: "werewolf.j2", Role.SEER: "seer.j2",
    Role.WITCH: "witch.j2", Role.HUNTER: "hunter.j2",
    Role.GUARD: "guard.j2", Role.VILLAGER: "villager.j2",
}

TEMPLATES_DIR = Path(__file__).parent / "prompts"

_ACTION_MESSAGES: dict[str, str] = {
    "night_kill": "请选择今晚的猎杀目标。",
    "night_check": "请选择今晚的查验目标。",
    "night_witch": "请决定是否使用解药或毒药。",
    "night_guard": "请选择今晚的守护目标。",
    "campaign_speech": "请发表你的警长竞选演讲。",
    "day_speech": "请发表你的发言（辩论模式：你可以回应之前发言的玩家）。",
    "vote": "请投出你的一票。",
    "death_shot": "请选择要开枪带走的玩家。",
    "sheriff_transfer": "请选择将警徽移交给哪位玩家。",
}

_ACTION_FALLBACKS: dict[str, str] = {
    "night_kill": "random", "night_check": "random",
    "night_witch": "pass", "night_guard": "random",
    "campaign_speech": "abstain", "day_speech": "abstain",
    "vote": "random", "death_shot": "pass", "sheriff_transfer": "pass",
}


class PlayerAgent(BaseAgent):
    """AI agent that plays a role with full information access via tools."""

    def __init__(self, config: GameConfig, agent_name: str = "player") -> None:
        super().__init__(config, agent_name)
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False,
        )
        self._template_cache: dict[str, jinja2.Template] = {}

    async def __call__(
        self,
        player_id: str,
        role: Role,
        game_state: GameState,
        action_type: str,
        prompt_override: str | None = None,
        debate_context: str = "",
    ) -> AgentResponse:
        template = self._get_template(role)
        context = self._build_context(player_id, role, game_state, action_type)
        system_prompt = template.render(**context)

        user_message = prompt_override or _ACTION_MESSAGES.get(action_type, f"请执行 {action_type}。")
        if debate_context:
            user_message = f"## 本轮已有发言（请辩论式回应）\n{debate_context}\n\n---\n{user_message}"

        fallback = _ACTION_FALLBACKS.get(action_type, "abstain")
        tool_context = self._build_tool_context(player_id, role, game_state)

        # Use streaming for speech actions
        if action_type in ("campaign_speech", "day_speech"):
            print(f"\n  {context.get('player_id', '?')}号: ", end="", flush=True)
            raw = await self.call_stream(system_prompt, user_message, tool_context)
            result = AgentResponse(action="speak", dialogue=raw.strip(), raw_response=raw)
        else:
            result = await self.call(
                system_prompt=system_prompt,
                user_message=user_message,
                default_action=fallback,
                tool_context=tool_context,
            )

        # Speech actions: use raw text as dialogue if JSON parsing failed
        if action_type in ("campaign_speech", "day_speech") and not result.dialogue:
            if result.raw_response and result.raw_response != result.reasoning:
                result.dialogue = result.raw_response[:500]
                result.action = "speak"

        return result

    # ── Combined context (public + private for template rendering) ──

    def _build_context(
        self, player_id: str, role: Role, state: GameState, action_type: str,
    ) -> dict[str, Any]:
        """Build template context with full public info + role-specific private info."""
        player = state.players.get(player_id)
        seat = str(player.seat_number) if player else player_id
        alive = sorted(state.alive_players(), key=lambda p: p.seat_number)
        dead = [p for p in state.players.values() if not p.is_alive]
        is_night = action_type in ("night_kill", "night_check", "night_witch", "night_guard")

        context: dict[str, Any] = {
            "role_name": self._role_name(role),
            "player_id": seat,
            "game_round": state.round_number,
            "alive_players": [f"{p.seat_number}号({ROLE_CHINESE.get(p.role, '?')})" for p in alive],
            "alive_count": len(alive),
            "dead_players": [f"{p.seat_number}号({ROLE_CHINESE.get(p.role, '?')})" for p in dead],
            "game_history": self._build_game_history(state),
            "sheriff": self._sheriff_display(state),
            "phase": "night" if is_night else "day",
            "memories": None,
        }

        # Role-specific private info (also available via tools)
        if role == Role.WEREWOLF:
            context["team"] = [
                str(p.seat_number) for p in state.alive_werewolves() if p.player_id != player_id
            ]
        elif role == Role.SEER:
            context["previous_checks"] = self._build_seer_checks(state)
        elif role == Role.WITCH:
            context["antidote_used"] = state.witch_antidote_used
            context["poison_used"] = state.witch_poison_used
            context["tonight_kill_target"] = self._get_witch_kill_info(state)
        elif role == Role.HUNTER:
            context["gun_active"] = True
        elif role == Role.GUARD:
            context["last_protected"] = self._get_guard_last_protect(state)

        return context

    def _get_witch_kill_info(self, state: GameState) -> str | None:
        if state.witch_antidote_used or state.night_kill_target is None:
            return None
        target = state.players.get(state.night_kill_target)
        return str(target.seat_number) if target else None

    def _get_guard_last_protect(self, state: GameState) -> str | None:
        if state.guard_last_protect is None:
            return None
        target = state.players.get(state.guard_last_protect)
        return str(target.seat_number) if target else None

    # ── Tool context (private info, accessible via get_my_private_info) ──

    def _build_tool_context(self, player_id: str, role: Role, state: GameState) -> dict:
        player = state.players.get(player_id)
        seat = str(player.seat_number) if player else "?"

        context: dict[str, Any] = {
            "my_role": ROLE_CHINESE.get(role, "unknown"),
            "my_seat": seat,
            "teammates": [],
            "previous_checks": [],
            "antidote_used": state.witch_antidote_used,
            "poison_used": state.witch_poison_used,
            "gun_active": True,
            "last_protected": None,
        }

        if role == Role.WEREWOLF:
            context["teammates"] = [
                str(p.seat_number) for p in state.alive_werewolves() if p.player_id != player_id
            ]
        if role == Role.SEER:
            context["previous_checks"] = self._build_seer_checks(state)
        if role == Role.GUARD and state.guard_last_protect:
            target = state.players.get(state.guard_last_protect)
            if target:
                context["last_protected"] = str(target.seat_number)

        # Full public info embedded in tool context for queries
        context["alive_players"] = [
            f"{p.seat_number}号" for p in sorted(state.alive_players(), key=lambda x: x.seat_number)
        ]
        context["dead_players"] = [
            f"{p.seat_number}号" for p in state.players.values() if not p.is_alive
        ]
        context["game_history"] = self._build_game_history(state)
        context["round_number"] = state.round_number
        context["sheriff"] = self._sheriff_display(state)

        return context

    def _build_seer_checks(self, state: GameState) -> list[dict[str, str]]:
        checks: list[dict[str, str]] = []
        for checked_id, camp in state.seer_checks.items():
            target = state.players.get(checked_id)
            if target:
                checks.append({
                    "player_id": str(target.seat_number),
                    "result": "狼人" if camp == Camp.WEREWOLF else "好人",
                })
        return checks

    def _build_game_history(self, state: GameState) -> str:
        if state.round_history:
            return "\n\n".join(state.round_history)
        return "游戏刚开始"

    @staticmethod
    def _sheriff_display(state: GameState) -> str | None:
        if state.sheriff_id and state.sheriff_id in state.players:
            p = state.players[state.sheriff_id]
            return f"{p.seat_number}号玩家" if p.is_alive else f"原警长{p.seat_number}号(已死亡)"
        return None

    def _get_template(self, role: Role) -> jinja2.Template:
        name = ROLE_TEMPLATE.get(role, "villager.j2")
        if name not in self._template_cache:
            self._template_cache[name] = self._env.get_template(name)
        return self._template_cache[name]

    @staticmethod
    def _role_name(role: Role) -> str:
        return ROLE_CHINESE.get(role, role.value)


player_agent: PlayerAgent | None = None
