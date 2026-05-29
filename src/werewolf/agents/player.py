"""Player agent for AI Werewolf game.

Each PlayerAgent instance represents one player with a specific role.
Uses Jinja2 templates for prompt construction and delegates to the base
LLM agent for decision-making.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import jinja2

from werewolf.agents.base import AgentResponse, BaseAgent
from werewolf.config import GameConfig
from werewolf.engine.state import Camp, GameState, Phase, PlayerState, Role

logger = logging.getLogger(__name__)

# ── Role display names ────────────────────────────────────────────

ROLE_CHINESE: dict[Role, str] = {
    Role.WEREWOLF: "狼人",
    Role.SEER: "预言家",
    Role.WITCH: "女巫",
    Role.HUNTER: "猎人",
    Role.GUARD: "守卫",
    Role.VILLAGER: "平民",
}

# ── Role → template mapping ───────────────────────────────────────

ROLE_TEMPLATE: dict[Role, str] = {
    Role.WEREWOLF: "werewolf.j2",
    Role.SEER: "seer.j2",
    Role.WITCH: "witch.j2",
    Role.HUNTER: "hunter.j2",
    Role.GUARD: "guard.j2",
    Role.VILLAGER: "villager.j2",
}

TEMPLATES_DIR = Path(__file__).parent / "prompts"

# Action-type specific user messages
_ACTION_MESSAGES: dict[str, str] = {
    "night_kill": "请选择今晚的猎杀目标。",
    "night_check": "请选择今晚的查验目标。",
    "night_witch": "请决定是否使用解药或毒药。",
    "night_guard": "请选择今晚的守护目标。",
    "campaign_speech": "请发表你的警长竞选演讲，决定是否参选。",
    "day_speech": "现在轮到你发言，请说出你的分析和判断。",
    "vote": "请投出你的一票，选择要放逐的玩家。",
    "death_shot": "你即将死亡，请选择要开枪带走的玩家。",
    "sheriff_transfer": "你即将死亡，请选择将警徽移交给哪位玩家（或输入 null 撕毁警徽）。",
}

# Fallback actions when JSON parsing fails
_ACTION_FALLBACKS: dict[str, str] = {
    "night_kill": "random",
    "night_check": "random",
    "night_witch": "pass",
    "night_guard": "random",
    "campaign_speech": "abstain",
    "day_speech": "abstain",
    "vote": "random",
    "death_shot": "pass",
    "sheriff_transfer": "pass",
}


class PlayerAgent(BaseAgent):
    """AI agent that plays a specific role in the Werewolf game.

    Uses role-specific Jinja2 templates to build system prompts, then
    delegates to the LLM for decision-making.

    Implements the callable protocol expected by GameOrchestrator:
        async def __call__(self, player_id, role, game_state, action_type) -> AgentResponse
    """

    def __init__(self, config: GameConfig, agent_name: str = "player") -> None:
        super().__init__(config, agent_name)
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=False,
        )
        self._template_cache: dict[str, jinja2.Template] = {}

    async def __call__(
        self,
        player_id: str,
        role: Role,
        game_state: GameState,
        action_type: str,
        prompt_override: str | None = None,
    ) -> AgentResponse:
        """Make a player decision for the given action type.

        Args:
            player_id: ID of the player making the decision (e.g. "player_1").
            role: The player's role.
            game_state: Current game state.
            action_type: One of "night_kill", "night_check", "night_witch",
                         "night_guard", "campaign_speech", "day_speech",
                         "vote", "death_shot", "sheriff_transfer".
            prompt_override: If provided, overrides the standard user message.

        Returns:
            AgentResponse with the player's decision.
        """
        template = self._get_template(role)
        context = self._build_context(player_id, role, game_state, action_type)

        system_prompt = template.render(**context)
        user_message = prompt_override or _ACTION_MESSAGES.get(
            action_type, f"请执行 {action_type} 行动。"
        )
        fallback = _ACTION_FALLBACKS.get(action_type, "abstain")

        return await self.call(
            system_prompt=system_prompt,
            user_message=user_message,
            default_action=fallback,
        )

    # ── Context builders ───────────────────────────────────────────

    def _build_context(
        self,
        player_id: str,
        role: Role,
        state: GameState,
        action_type: str,
    ) -> dict[str, Any]:
        """Build the Jinja2 template context for the player's role and action."""
        player = state.players.get(player_id)
        seat_number = str(player.seat_number) if player else player_id

        alive = sorted(
            state.alive_players(), key=lambda p: p.seat_number
        )
        alive_ids = [str(p.seat_number) for p in alive]

        is_night = action_type in (
            "night_kill",
            "night_check",
            "night_witch",
            "night_guard",
        )

        context: dict[str, Any] = {
            "role_name": self._role_name(role),
            "player_id": seat_number,
            "game_round": state.round_number,
            "alive_players": alive_ids,
            "memories": None,
            "game_history": self._build_game_history(state),
            "phase": "night" if is_night else "day",
        }

        # ── Role-specific visibility ──
        if role == Role.WEREWOLF:
            context["team"] = [
                str(p.seat_number)
                for p in state.alive_werewolves()
                if p.player_id != player_id
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

    def _build_seer_checks(self, state: GameState) -> list[dict[str, str]]:
        """Build seer's historical check results with seat numbers."""
        checks: list[dict[str, str]] = []
        for checked_id, camp in state.seer_checks.items():
            target = state.players.get(checked_id)
            if target is not None:
                checks.append({
                    "player_id": str(target.seat_number),
                    "result": "werewolf" if camp == Camp.WEREWOLF else "good",
                })
        return checks

    def _get_witch_kill_info(self, state: GameState) -> str | None:
        """Return tonight's kill target seat number for the witch, or None."""
        if state.witch_antidote_used:
            return None
        if state.night_kill_target is None:
            return None
        target = state.players.get(state.night_kill_target)
        if target is None:
            return None
        return str(target.seat_number)

    def _get_guard_last_protect(self, state: GameState) -> str | None:
        """Return the last protected player's seat number for the guard, or None."""
        if state.guard_last_protect is None:
            return None
        target = state.players.get(state.guard_last_protect)
        if target is None:
            return None
        return str(target.seat_number)

    # ── History summarisation ──────────────────────────────────────

    def _build_game_history(self, state: GameState) -> str:
        """Summarise recent public events for the template context."""
        parts: list[str] = []

        if state.eliminated_tonight:
            deaths = [
                self._seat_display(pid, state) for pid in state.eliminated_tonight
            ]
            parts.append(f"昨晚死亡: {', '.join(deaths)}")
        if state.eliminated_today:
            deaths = [
                self._seat_display(pid, state) for pid in state.eliminated_today
            ]
            parts.append(f"今日被放逐: {', '.join(deaths)}")
        if state.sheriff_id:
            sheriff = state.players.get(state.sheriff_id)
            if sheriff and sheriff.is_alive:
                parts.append(f"当前警长: {sheriff.seat_number}号玩家")

        return "\n".join(parts) if parts else "暂无重大事件"

    @staticmethod
    def _seat_display(player_id: str, state: GameState) -> str:
        """Convert a player_id to a human-readable seat number string."""
        player = state.players.get(player_id)
        if player is not None:
            return f"{player.seat_number}号玩家"
        return player_id

    # ── Template loading ────────────────────────────────────────────

    def _get_template(self, role: Role) -> jinja2.Template:
        """Load and cache the Jinja2 template for the given role."""
        template_name = ROLE_TEMPLATE.get(role, "villager.j2")
        if template_name not in self._template_cache:
            self._template_cache[template_name] = self._env.get_template(
                template_name
            )
        return self._template_cache[template_name]

    # ── Static helpers ──────────────────────────────────────────────

    @staticmethod
    def _role_name(role: Role) -> str:
        """Return the Chinese display name for a role."""
        return ROLE_CHINESE.get(role, role.value)


# Module-level sentinel for lazy import by GameOrchestrator.
# Set to a configured PlayerAgent instance before use, or leave as None
# (the orchestrator gracefully handles None by returning default responses).
player_agent: PlayerAgent | None = None
