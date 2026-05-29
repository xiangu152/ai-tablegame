"""Judge (Game Master) agent for AI Werewolf game.

The JudgeAgent narrates game events, announces deaths, and guides the game
flow. It validates LLM-generated narrations against required facts and falls
back to deterministic template-based narration when validation fails.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import jinja2

from werewolf.agents.base import AgentResponse, BaseAgent
from werewolf.config import GameConfig
from werewolf.engine.state import GameState, Phase

logger = logging.getLogger(__name__)

# ── Phase display names ────────────────────────────────────────────

PHASE_CHINESE: dict[Phase, str] = {
    Phase.SETUP: "游戏准备",
    Phase.SHERIFF_ELECTION: "警长竞选",
    Phase.NIGHT_WEREWOLF: "狼人行动阶段",
    Phase.NIGHT_SEER: "预言家行动阶段",
    Phase.NIGHT_WITCH: "女巫行动阶段",
    Phase.NIGHT_HUNTER: "猎人确认阶段",
    Phase.NIGHT_GUARD: "守卫行动阶段",
    Phase.DAY_DEATH_ANNOUNCE: "天亮公布死讯",
    Phase.DAY_DISCUSSION: "白天发言环节",
    Phase.DAY_VOTE: "白天投票环节",
    Phase.GAME_END: "游戏结束",
}

TEMPLATES_DIR = Path(__file__).parent / "prompts"
JUDGE_TEMPLATE = "judge.j2"


class JudgeAgent(BaseAgent):
    """AI Game Master that narrates the Werewolf game.

    Builds a constraints block (MUST_ANNOUNCE) from the game state, renders
    the judge.j2 template as a system prompt, and validates the LLM output
    against required facts.

    Implements the callable protocol expected by GameOrchestrator:
        async def __call__(self, phase, game_state) -> AgentResponse
    """

    def __init__(self, config: GameConfig, agent_name: str = "judge") -> None:
        super().__init__(config, agent_name)
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=False,
        )
        self._template = self._env.get_template(JUDGE_TEMPLATE)

    async def __call__(
        self,
        phase: Phase,
        game_state: GameState,
    ) -> AgentResponse:
        """Narrate the current game phase.

        Args:
            phase: Current game phase to narrate.
            game_state: Current game state.

        Returns:
            AgentResponse with dialogue set to the narration text.
        """
        must_announce = self._build_must_announce(phase, game_state)
        context = self._build_context(phase, game_state, must_announce)

        system_prompt = self._template.render(**context)

        response = await self.call(
            system_prompt=system_prompt,
            user_message=f"请主持游戏 {phase.name} 阶段的叙事。",
            default_action="narrate",
        )

        # Try to extract narration from the LLM response
        narration = self._extract_narration(response.raw_response)
        if narration is None:
            logger.warning(
                "Judge could not extract narration from LLM response, using fallback"
            )
            narration = self._template_narration(phase, must_announce)

        # Validate the narration
        if not self._validate_narration(narration, must_announce):
            logger.warning(
                "Judge narration failed validation for phase %s, using fallback",
                phase.name,
            )
            narration = self._template_narration(phase, must_announce)

        return AgentResponse(
            action="narrate",
            dialogue=narration,
            reasoning="",
            raw_response=response.raw_response,
        )

    # ── Context builders ────────────────────────────────────────────

    def _build_context(
        self,
        phase: Phase,
        state: GameState,
        must_announce: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the Jinja2 template context for the judge."""
        alive = sorted(
            state.alive_players(), key=lambda p: p.seat_number
        )
        alive_ids = [str(p.seat_number) for p in alive]

        return {
            "role_name": "游戏主持人",
            "game_round": state.round_number,
            "current_phase": PHASE_CHINESE.get(phase, phase.name),
            "alive_players": alive_ids,
            "memories": None,
            "game_history": self._build_game_history(state),
            "must_announce": must_announce,
        }

    def _build_must_announce(
        self, phase: Phase, state: GameState
    ) -> dict[str, Any]:
        """Build the MUST_ANNOUNCE constraint block from game state.

        Contains all facts that the judge must faithfully include in the
        narration. This is rendered as JSON in the template prompt.
        """
        alive = sorted(
            state.alive_players(), key=lambda p: p.seat_number
        )
        alive_numbers = [p.seat_number for p in alive]

        announcement: dict[str, Any] = {
            "phase": PHASE_CHINESE.get(phase, phase.name),
            "round": state.round_number,
            "alive_players": alive_numbers,
            "alive_count": len(alive_numbers),
        }

        if state.sheriff_id is not None:
            sheriff = state.players.get(state.sheriff_id)
            if sheriff and sheriff.is_alive:
                announcement["sheriff"] = sheriff.seat_number

        # Phase-specific death announce
        if phase == Phase.DAY_DEATH_ANNOUNCE:
            deaths = state.eliminated_tonight
            if deaths:
                announcement["deaths"] = [
                    self._player_seat(pid, state) for pid in deaths
                ]
            else:
                announcement["deaths"] = []

        elif phase == Phase.DAY_VOTE:
            if state.eliminated_today:
                eliminated = state.eliminated_today[-1]
                player = state.players.get(eliminated)
                if player:
                    announcement["eliminated_today"] = {
                        "seat": player.seat_number,
                        "role": player.role.value,
                    }
            else:
                announcement["eliminated_today"] = None

        elif phase == Phase.GAME_END:
            announcement["final_round"] = state.round_number

        return announcement

    def _build_game_history(self, state: GameState) -> str:
        """Summarise recent public events for the judge."""
        parts: list[str] = []
        if state.eliminated_tonight:
            deaths = [
                self._seat_display(pid, state)
                for pid in state.eliminated_tonight
            ]
            parts.append(f"昨晚死亡: {', '.join(deaths)}")
        if state.eliminated_today:
            deaths = [
                self._seat_display(pid, state)
                for pid in state.eliminated_today
            ]
            parts.append(f"今日被放逐: {', '.join(deaths)}")
        return "\n".join(parts) if parts else "暂无"

    # ── Validation ──────────────────────────────────────────────────

    def _validate_narration(
        self, narration: str, must_announce: dict[str, Any]
    ) -> bool:
        """Validate that the narration contains all required facts.

        Checks:
        - If there are deaths to announce, the narration mentions them.
        - If there is an elimination today, the narration mentions it.
        - The narration is non-empty.

        Returns True if validation passes, False otherwise.
        """
        if not narration or not narration.strip():
            return False

        # Check that announced deaths appear in the narration
        deaths = must_announce.get("deaths", [])
        for death_seat in deaths:
            seat_str = str(death_seat)
            if seat_str not in narration:
                logger.warning(
                    "Judge narration missing death of player %s", seat_str
                )
                return False

        # Check that eliminated player is mentioned
        eliminated = must_announce.get("eliminated_today")
        if eliminated is not None and isinstance(eliminated, dict):
            seat_str = str(eliminated.get("seat", ""))
            if seat_str and seat_str not in narration:
                logger.warning(
                    "Judge narration missing elimination of player %s", seat_str
                )
                return False

        return True

    # ── Fallback narration ──────────────────────────────────────────

    def _template_narration(
        self, phase: Phase, must_announce: dict[str, Any]
    ) -> str:
        """Generate deterministic fallback narration from MUST_ANNOUNCE.

        Used when LLM response parsing or validation fails. Produces a
        minimal but factually correct narration.
        """
        phase_name = must_announce.get("phase", "")
        alive = must_announce.get("alive_players", [])
        alive_str = "、".join(str(s) for s in alive)

        if phase == Phase.DAY_DEATH_ANNOUNCE:
            deaths = must_announce.get("deaths", [])
            if deaths:
                death_str = "、".join(f"{s}号" for s in deaths)
                return (
                    f"天亮了，昨晚{death_str}玩家死亡。"
                    f"存活玩家: {alive_str}号。"
                    f"现在开始发言环节。"
                )
            else:
                return (
                    f"天亮了，昨晚是平安夜，没有玩家死亡。"
                    f"存活玩家: {alive_str}号。"
                    f"现在开始发言环节。"
                )

        elif phase == Phase.DAY_DISCUSSION:
            sheriff = must_announce.get("sheriff")
            if sheriff:
                return (
                    f"现在是第{must_announce.get('round', '')}轮白天发言环节。"
                    f"警长为{sheriff}号玩家。"
                    f"请按照发言顺序依次发言。"
                )
            return (
                f"现在是第{must_announce.get('round', '')}轮白天发言环节。"
                f"请按照发言顺序依次发言。"
            )

        elif phase == Phase.DAY_VOTE:
            eliminated = must_announce.get("eliminated_today")
            if eliminated and isinstance(eliminated, dict):
                seat = eliminated.get("seat", "")
                role = eliminated.get("role", "")
                return f"{seat}号玩家被公投出局，身份是{role}。"
            return "本轮投票无人被淘汰，进入夜晚。"

        elif phase == Phase.GAME_END:
            return (
                f"游戏结束。感谢各位玩家的参与。"
                f"最终存活玩家: {alive_str}号。"
            )

        elif phase == Phase.SHERIFF_ELECTION:
            return (
                f"现在开始警长竞选环节。"
                f"请想要竞选警长的玩家举手参选。"
            )

        elif phase == Phase.NIGHT_WEREWOLF:
            return "天黑请闭眼。狼人请睁眼，请选择今晚的猎杀目标。"

        elif phase == Phase.NIGHT_SEER:
            return "预言家请睁眼，请选择今晚的查验目标。"

        elif phase == Phase.NIGHT_WITCH:
            return "女巫请睁眼，请决定是否使用解药或毒药。"

        elif phase == Phase.NIGHT_GUARD:
            return "守卫请睁眼，请选择今晚的守护目标。"

        elif phase == Phase.NIGHT_HUNTER:
            return "猎人请睁眼，确认你的开枪状态。"

        elif phase == Phase.SETUP:
            return "游戏准备就绪，12名玩家已就位。游戏即将开始。"

        # Generic fallback
        return f"现在是{phase_name}阶段。存活玩家: {alive_str}号。"

    # ── Response parsing ────────────────────────────────────────────

    def _extract_narration(self, raw_response: str) -> str | None:
        """Extract the narration field from the judge's JSON response.

        The judge template asks for JSON with 'narration' and 'phase_summary'
        fields, not the standard 'action'/'target' format.
        """
        if not raw_response:
            return None

        json_str = self._extract_json(raw_response)
        if json_str is None:
            return None

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        narration = data.get("narration", "")
        if isinstance(narration, str) and narration.strip():
            return narration.strip()

        return None

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _player_seat(player_id: str, state: GameState) -> int | None:
        """Get a player's seat number from their player_id."""
        player = state.players.get(player_id)
        if player is not None:
            return player.seat_number
        return None

    @staticmethod
    def _seat_display(player_id: str, state: GameState) -> str:
        """Convert a player_id to a human-readable seat number string."""
        player = state.players.get(player_id)
        if player is not None:
            return f"{player.seat_number}号玩家"
        return player_id


# Module-level sentinel for lazy import by GameOrchestrator.
# Set to a configured JudgeAgent instance before use, or leave as None
# (the orchestrator gracefully handles None by returning default responses).
judge_agent: JudgeAgent | None = None
