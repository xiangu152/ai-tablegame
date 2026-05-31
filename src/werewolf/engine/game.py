"""Game orchestrator for the AI Werewolf game.

Runs the complete game loop: setup, sheriff election, night phases,
day discussion/vote, victory checks, and game end.

Designed to work with BOTH real AI agents and mock agents for testing.
Agents are injected as callables, not created by the orchestrator.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import datetime
from typing import Any, Callable, Awaitable

from werewolf.config import GameConfig
from werewolf.engine.state import (
    Camp,
    GAME_MODES,
    GameState,
    Phase,
    PlayerState,
    Role,
    STANDARD_12P,
    get_camp,
)
from werewolf.engine.phases import (
    setup_phase,
    resolve_sheriff_election,
    night_werewolf_phase,
    night_seer_phase,
    night_witch_phase,
    night_guard_phase,
    resolve_night_deaths,
    day_discussion_phase,
    resolve_vote,
    resolve_hunter_death,
    wolf_self_destruct,
    STANDARD_NIGHT_ORDER,
)
from werewolf.engine.rules import RulesEngine
from werewolf.storage.repository import Repository
from werewolf.storage.models import (
    GameRecord,
    PlayerRecord,
    RoundRecord,
    EventRecord,
    DialogueRecord,
    VoteRecord,
    GameResult,
)
from werewolf.agents.base import AgentResponse

logger = logging.getLogger(__name__)


# ── Default agent imports (lazy, may fail if not yet implemented) ──

_player_agent_cache: Callable | None | bool = None
_judge_agent_cache: Callable | None | bool = None


def _get_default_player_agent():
    """Lazy-import the default player agent from werewolf.agents.player."""
    global _player_agent_cache
    if _player_agent_cache is None:
        try:
            from werewolf.agents.player import player_agent as pa
            _player_agent_cache = pa
        except ImportError:
            _player_agent_cache = False
    return _player_agent_cache if _player_agent_cache is not False else None


def _get_default_judge_agent():
    """Lazy-import the default judge agent from werewolf.agents.judge."""
    global _judge_agent_cache
    if _judge_agent_cache is None:
        try:
            from werewolf.agents.judge import judge_agent as ja
            _judge_agent_cache = ja
        except ImportError:
            _judge_agent_cache = False
    return _judge_agent_cache if _judge_agent_cache is not False else None


# ── Agent protocol types ───────────────────────────────────────────

PlayerAgent = Callable[
    [str, Role, GameState, str, str | None],
    Awaitable[AgentResponse],
]
"""Player agent callable signature.

Args:
    player_id: ID of the player making the decision.
    role: Role of the player.
    game_state: Current game state.
    action_type: Type of action (e.g. "campaign_speech", "night_kill", "vote").

Returns:
    AgentResponse with the player's decision.
"""

JudgeAgent = Callable[
    [Phase, GameState],
    Awaitable[AgentResponse],
]
"""Judge agent callable signature.

Args:
    phase: Current phase being narrated.
    game_state: Current game state.

Returns:
    AgentResponse with the judge's narration.
"""


# ── Orchestrator ───────────────────────────────────────────────────

class GameOrchestrator:
    """Runs the complete Werewolf game loop.

    Agents are injected as callables:
      - player_agent: called for every player decision (speech, vote, night action).
      - judge_agent: called for every phase transition narration.

    If either agent is None, those calls are silently skipped (useful for testing).
    """

    def __init__(
        self,
        config: GameConfig,
        repo: Repository,
        player_agent: PlayerAgent | None = None,
        judge_agent: JudgeAgent | None = None,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.repo = repo
        self._player_agent = player_agent
        self._judge_agent = judge_agent
        self._on_progress = on_progress

    # ── Agent accessors ────────────────────────────────────────────

    @property
    def player_agent(self) -> PlayerAgent | None:
        """Return the injected player agent, or the default if not set."""
        if self._player_agent is not None:
            return self._player_agent
        return _get_default_player_agent()

    @property
    def judge_agent(self) -> JudgeAgent | None:
        """Return the injected judge agent, or the default if not set."""
        if self._judge_agent is not None:
            return self._judge_agent
        return _get_default_judge_agent()

    # ── Progress callback ──────────────────────────────────────────

    def _emit_progress(self, event: str, **kwargs: Any) -> None:
        """Emit a progress event to the optional callback."""
        if self._on_progress is not None:
            self._on_progress(event, {**kwargs})

    # ── Main game loop ─────────────────────────────────────────────

    async def run_game(self, game_id: str | None = None) -> GameState:
        """Run one complete game. Returns the final GameState."""
        if game_id is None:
            game_id = self._new_id()

        # ── Step 1: SETUP ──
        role_config = GAME_MODES.get(self.config.game_mode, STANDARD_12P)
        state = GameState(game_id=game_id, players={})
        setup_phase(state, role_config)
        self._log_game_start(game_id)
        self._persist_players(state, game_id)
        logger.info("Game %s started — 12 players assigned roles", game_id)

        setup_round_id = self._create_round(state, Phase.SETUP)
        await self._narrate(Phase.SETUP, state, setup_round_id)

        self._emit_progress("game_start",
            game_id=game_id,
            werewolf_count=len(state.alive_werewolves()),
            good_count=len(state.alive_good_players()),
            total=state.alive_count(),
        )

        # ── Step 2: SHERIFF_ELECTION (round 1 only) ──
        await self._run_sheriff_election(state, game_id)

        # ── Steps 3-7: Main day-night cycles ──
        while True:
            state.round_number += 1
            round_id = self._create_round(state, Phase.NIGHT_WEREWOLF)

            self._emit_progress("round_start",
                round_number=state.round_number,
                alive_count=state.alive_count(),
                game_id=game_id,
            )

            # Step 3: NIGHT PHASES
            self._emit_progress("phase", phase="night", game_id=game_id)
            await self._run_night_phases(state, round_id)

            # Step 4: DAY DEATH ANNOUNCE
            self._emit_progress("phase", phase="day_death_announce", game_id=game_id)
            await self._run_day_death_announce(state, round_id)

            # Step 5: DAY DISCUSSION
            self._emit_progress("phase", phase="day_discussion", game_id=game_id)
            await self._run_day_discussion(state, round_id)

            # Step 6: DAY VOTE
            self._emit_progress("phase", phase="day_vote", game_id=game_id)
            await self._run_day_vote(state, round_id)

            # Step 7: VICTORY CHECK
            winner, reason = RulesEngine.check_victory(
                state, self.config.round_limit
            )
            if winner is not None or state.round_number >= self.config.round_limit:
                state.phase = Phase.GAME_END
                self._emit_progress("game_end",
                    winner=winner or "stalemate",
                    reason=reason,
                    total_rounds=state.round_number,
                    game_id=game_id,
                )
                logger.info(
                    "Game %s ended: winner=%s, reason=%s, rounds=%d",
                    game_id,
                    winner or "stalemate",
                    reason,
                    state.round_number,
                )
                break

            state.round_history.append(self._build_round_summary(state, round_id))

            logger.info(
                "Game %s round %d complete — no winner yet, continuing",
                game_id,
                state.round_number,
            )
            self._emit_progress("round_end",
                round_number=state.round_number,
                alive_count=state.alive_count(),
                game_id=game_id,
            )

        # ── Step 8: GAME_END ──
        await self._finalize_game(state, game_id, winner, reason)
        return state

    # ── Sheriff election ───────────────────────────────────────────

    async def _run_sheriff_election(
        self, state: GameState, game_id: str
    ) -> None:
        """Run the sheriff election phase (round 1 only)."""
        round_id = self._create_round(state, Phase.SHERIFF_ELECTION)
        state.phase = Phase.SHERIFF_ELECTION

        self._emit_progress("phase", phase="sheriff_election", game_id=game_id)

        await self._narrate(
            Phase.SHERIFF_ELECTION, state, round_id,
            prompt_override="现在开始警长竞选",
        )

        # Collect candidates — every player gets a chance to speak
        candidates: list[str] = []
        alive = state.alive_players()

        for player in alive:
            response = await self._call_player(
                player.player_id, player.role, state, "campaign_speech",
            )
            if response.action == "self_destruct" and player.role == Role.WEREWOLF:
                wolf_self_destruct(state, player.player_id, during_election=True)
                self._log_event(round_id, "wolf_self_destruct", player.player_id,
                                {"during_election": True})
                logger.info("Wolf %s self-destructed during election", player.player_id)
                return

            # Accept as candidate if they produced speech content, regardless of action keyword
            speech = response.dialogue or response.reasoning or ""
            if speech.strip():
                candidates.append(player.player_id)
                self._log_dialogue(round_id, player.player_id,
                                   response.dialogue, response.reasoning)
                logger.info("Candidate %s spoke: %s", player.player_id, speech[:80])

        if not candidates:
            logger.info("No sheriff candidates — election skipped")
            state.sheriff_election_done = True
            return

        # Hold election (up to 2 voting rounds)
        for attempt in range(2):
            votes = await self._collect_sheriff_votes(
                state, candidates, round_id, attempt + 1,
            )
            resolve_sheriff_election(state, votes, candidates)

            if state.sheriff_id is not None:
                break

            if attempt == 0:
                logger.info("Sheriff election tie — re-voting")

        if state.sheriff_id is None:
            await self._narrate(
                Phase.SHERIFF_ELECTION, state, round_id,
                prompt_override="警长竞选平票，本轮没有警长",
            )
            logger.info("Sheriff election ended with no sheriff (警徽流失)")
        else:
            sheriff_seat = state.players[state.sheriff_id].seat_number
            await self._narrate(
                Phase.SHERIFF_ELECTION, state, round_id,
                prompt_override=f"{sheriff_seat}号玩家当选警长",
            )
            logger.info("Sheriff elected: %s", state.sheriff_id)

        state.sheriff_election_done = True

    async def _collect_sheriff_votes(
        self,
        state: GameState,
        candidates: list[str],
        round_id: str,
        attempt: int,
    ) -> dict[str, str]:
        """Collect sheriff election votes from non-candidate players."""
        candidate_set = set(candidates)
        voters = [p for p in state.alive_players() if p.player_id not in candidate_set]

        if not voters:
            return {}

        votes: dict[str, str] = {}
        for voter in voters:
            response = await self._call_player(
                voter.player_id, voter.role, state, "vote",
                prompt_override=(
                    f"警长竞选第{attempt}轮投票，请选择一位候选人："
                    f"{', '.join(candidates)}"
                ),
            )
            target = response.target
            if target and target in candidate_set:
                votes[voter.player_id] = target
                self._log_vote(round_id, voter.player_id, target, "sheriff_election")
                logger.info("Sheriff vote: %s -> %s", voter.player_id, target)
            else:
                logger.info("Player %s abstained from sheriff vote", voter.player_id)

        return votes

    # ── Night phases ───────────────────────────────────────────────

    async def _run_night_phases(
        self, state: GameState, round_id: str
    ) -> None:
        """Run all night phases in STANDARD_NIGHT_ORDER."""
        await self._narrate(
            Phase.SETUP, state, round_id,
            prompt_override="天黑请闭眼",
        )

        for phase in STANDARD_NIGHT_ORDER:
            state.phase = phase

            if phase == Phase.NIGHT_WEREWOLF:
                await self._run_night_werewolf(state, round_id)
            elif phase == Phase.NIGHT_SEER:
                await self._run_night_seer(state, round_id)
            elif phase == Phase.NIGHT_WITCH:
                await self._run_night_witch(state, round_id)
            elif phase == Phase.NIGHT_HUNTER:
                await self._run_night_hunter(state, round_id)
            elif phase == Phase.NIGHT_GUARD:
                await self._run_night_guard(state, round_id)

    async def _run_night_werewolf(
        self, state: GameState, round_id: str
    ) -> None:
        """Run the werewolf night kill phase."""
        wolves = state.alive_werewolves()
        if not wolves:
            return

        await self._narrate(
            Phase.NIGHT_WEREWOLF, state, round_id,
            prompt_override="狼人请睁眼，请选择袭击目标",
        )

        # Call all alive werewolves in parallel
        async def ask_wolf(wolf: PlayerState) -> tuple[str, AgentResponse]:
            response = await self._call_player(
                wolf.player_id, wolf.role, state, "night_kill",
            )
            return wolf.player_id, response

        results = await asyncio.gather(*(ask_wolf(w) for w in wolves))

        # Log all wolf decisions
        suggestions: dict[str, list[str]] = {}  # target -> [wolf_ids]
        for wolf_id, response in results:
            target = response.target
            if target and target in state.players and state.players[target].is_alive:
                suggestions.setdefault(target, []).append(wolf_id)
                self._log_event(
                    round_id, "night_kill_decision", wolf_id,
                    {"target": target, "reasoning": response.reasoning},
                )
                logger.info("Wolf %s suggested kill target: %s", wolf_id, target)

        # Determine kill target: majority vote, else random, else fallback
        kill_target = None
        if suggestions:
            best_target = None
            best_count = 0
            all_suggestions: list[str] = []
            for target, wolf_ids in suggestions.items():
                all_suggestions.extend([target] * len(wolf_ids))
                if len(wolf_ids) > best_count:
                    best_count = len(wolf_ids)
                    best_target = target

            majority_threshold = len(wolves) // 2 + 1
            if best_count >= majority_threshold:
                kill_target = best_target
                logger.info("Werewolf majority kill (%d/%d): %s", best_count, len(wolves), kill_target)
            else:
                kill_target = random.choice(all_suggestions)
                logger.info("Werewolf random kill (no majority): %s", kill_target)

        if kill_target is None:
            targets = [p for p in state.alive_players() if p.camp != Camp.WEREWOLF]
            if targets:
                kill_target = random.choice(targets).player_id
                logger.info("Werewolf fallback kill (random non-wolf): %s", kill_target)

        self._log_event(
            round_id, "night_kill_target", "system",
            {"target": kill_target, "suggestion_count": len(suggestions)},
        )
        night_werewolf_phase(state, kill_target)

    async def _run_night_seer(
        self, state: GameState, round_id: str
    ) -> None:
        """Run the seer's night check phase."""
        seers = [p for p in state.alive_players() if p.role == Role.SEER]
        if not seers:
            return

        seer = seers[0]
        await self._narrate(
            Phase.NIGHT_SEER, state, round_id,
            prompt_override="预言家请睁眼，请选择查验对象",
        )

        response = await self._call_player(
            seer.player_id, seer.role, state, "night_check",
        )
        target = response.target
        if not (target and target in state.players and state.players[target].is_alive):
            alive_others = [p for p in state.alive_players() if p.player_id != seer.player_id]
            target = random.choice(alive_others).player_id if alive_others else ""
            logger.info("Seer fallback check target: %s", target)
        camp = state.players[target].camp
        night_seer_phase(state, target, camp)
        self._log_event(
            round_id, "night_check", seer.player_id,
            {"target": target, "result": camp.value},
        )
        logger.info("Seer checked %s -> %s", target, camp.value)

    async def _run_night_witch(
        self, state: GameState, round_id: str
    ) -> None:
        """Run the witch's night action phase."""
        witches = [p for p in state.alive_players() if p.role == Role.WITCH]
        if not witches:
            return

        witch = witches[0]

        # Tell witch what happened tonight
        kill_info = ""
        if state.night_kill_target:
            kill_info = f"今晚{state.night_kill_target}号玩家被狼人袭击"
        else:
            kill_info = "今晚无人被狼人袭击"

        await self._narrate(
            Phase.NIGHT_WITCH, state, round_id,
            prompt_override=f"女巫请睁眼，{kill_info}",
        )

        response = await self._call_player(
            witch.player_id, witch.role, state, "night_witch",
            prompt_override=(
                f"今晚{kill_info}。"
                f"解药状态：{'已使用' if state.witch_antidote_used else '可用'}，"
                f"毒药状态：{'已使用' if state.witch_poison_used else '可用'}。"
                f"请决定是否使用解药或毒药。"
            ),
        )

        use_antidote = response.action in ("use_antidote", "save", "both")
        use_poison = response.action in ("use_poison", "both")
        poison_target = response.target if use_poison else None

        night_witch_phase(state, use_antidote, use_poison, poison_target)
        self._log_event(
            round_id, "night_witch", witch.player_id,
            {
                "use_antidote": use_antidote,
                "use_poison": use_poison,
                "poison_target": poison_target,
                "reasoning": response.reasoning,
            },
        )
        logger.info(
            "Witch %s: antidote=%s, poison=%s(%s)",
            witch.player_id, use_antidote, use_poison, poison_target,
        )

    async def _run_night_hunter(
        self, state: GameState, round_id: str
    ) -> None:
        """Inform the hunter of their gun status (passive — no action)."""
        hunters = [p for p in state.alive_players() if p.role == Role.HUNTER]
        if not hunters:
            return

        hunter = hunters[0]
        await self._narrate(
            Phase.NIGHT_HUNTER, state, round_id,
            prompt_override="猎人请睁眼，你的开枪状态：可以开枪",
        )
        # Passive: no player_agent call needed — the hunter merely
        # learns whether the gun is available. Actual shooting happens
        # during day elimination or death resolution.
        logger.info("Hunter %s informed of gun status at night", hunter.player_id)

    async def _run_night_guard(
        self, state: GameState, round_id: str
    ) -> None:
        """Run the guard's night protection phase."""
        guards = [p for p in state.alive_players() if p.role == Role.GUARD]
        if not guards:
            return

        guard = guards[0]
        await self._narrate(
            Phase.NIGHT_GUARD, state, round_id,
            prompt_override="守卫请睁眼，请选择守护对象",
        )

        response = await self._call_player(
            guard.player_id, guard.role, state, "night_guard",
        )
        target = response.target
        if target and target in state.players and state.players[target].is_alive:
            night_guard_phase(state, target)
            self._log_event(
                round_id, "night_guard", guard.player_id,
                {"target": target},
            )
            logger.info("Guard protected %s", target)

    # ── Day phases ─────────────────────────────────────────────────

    async def _run_day_death_announce(
        self, state: GameState, round_id: str
    ) -> None:
        """Resolve night deaths and announce results."""
        state.phase = Phase.DAY_DEATH_ANNOUNCE
        resolve_night_deaths(state)

        deaths = state.eliminated_tonight
        if deaths:
            death_list = "、".join(
                f"{pid}号玩家" for pid in deaths
            )
            message = f"天亮了，昨晚{death_list}死亡"
            logger.info("Night deaths: %s", deaths)
        else:
            message = "天亮了，昨晚是平安夜"
            logger.info("Peaceful night — no deaths")

        # Handle hunter death at night (no shot)
        for pid in deaths:
            player = state.players.get(pid)
            if player and player.role == Role.HUNTER:
                logger.info("Hunter %s died at night — no shot available", pid)

        await self._narrate(
            Phase.DAY_DEATH_ANNOUNCE, state, round_id,
            prompt_override=message,
        )
        state.eliminated_tonight = []

    async def _run_day_discussion(
        self, state: GameState, round_id: str
    ) -> None:
        """Run the day discussion phase with sequential debate."""
        state.phase = Phase.DAY_DISCUSSION

        speaking_order = day_discussion_phase(state)
        if not speaking_order:
            return

        await self._narrate(
            Phase.DAY_DISCUSSION, state, round_id,
            prompt_override="现在开始发言环节，请每位玩家根据之前发言进行辩论",
        )

        previous_speeches: list[str] = []

        for player_id in speaking_order:
            player = state.players.get(player_id)
            if not player or not player.is_alive:
                continue

            debate_context = "\n".join(previous_speeches) if previous_speeches else ""

            response = await self._call_player(
                player.player_id, player.role, state, "day_speech",
                debate_context=debate_context,
            )

            if response.action == "self_destruct" and player.role == Role.WEREWOLF:
                wolf_self_destruct(state, player.player_id, during_election=False)
                self._log_event(
                    round_id, "wolf_self_destruct", player.player_id,
                    {"during_election": False},
                )
                logger.info("Wolf %s self-destructed during discussion", player.player_id)
                return

            speech_text = response.dialogue or response.reasoning or ""
            previous_speeches.append(
                f"{player.seat_number}号玩家: {speech_text}"
            )

            self._log_dialogue(
                round_id, player.player_id, response.dialogue, response.reasoning,
            )
            logger.info("Speech by %s: %s", player_id, speech_text[:80])

        await self._narrate(
            Phase.DAY_DISCUSSION, state, round_id,
            prompt_override="发言环节结束",
        )

    async def _run_day_vote(
        self, state: GameState, round_id: str
    ) -> None:
        """Run the day elimination vote."""
        state.phase = Phase.DAY_VOTE
        state.current_votes = {}
        state.vote_round = 0

        await self._narrate(
            Phase.DAY_VOTE, state, round_id,
            prompt_override="现在开始投票，请所有存活玩家投票",
        )

        # Up to 2 re-vote attempts
        while state.vote_round < 3:
            votes = await self._collect_day_votes(state, round_id)
            state.current_votes = votes
            resolve_vote(state, votes)

            if state.eliminated_today:
                break

            if state.vote_round >= 3:
                logger.info("Vote ended with no elimination after 2 re-votes")
                break

            if state.vote_round > 0:
                logger.info("Re-vote attempt %d", state.vote_round)
                await self._narrate(
                    Phase.DAY_VOTE, state, round_id,
                    prompt_override="投票平票，请重新投票",
                )

        # Announce result
        if state.eliminated_today:
            eliminated_id = state.eliminated_today[-1]
            player = state.players[eliminated_id]
            await self._narrate(
                Phase.DAY_VOTE, state, round_id,
                prompt_override=(
                    f"{player.seat_number}号玩家被公投出局，"
                    f"身份是{player.role.value}"
                ),
            )
            logger.info(
                "Player %s eliminated by vote (role: %s)",
                eliminated_id, player.role.value,
            )

            # Handle hunter retaliation
            if player.role == Role.HUNTER:
                await self._run_hunter_death_shot(
                    state, eliminated_id, round_id,
                )

            # Handle sheriff badge transfer
            if player.is_sheriff:
                await self._handle_sheriff_transfer(
                    state, eliminated_id, round_id,
                )
        else:
            await self._narrate(
                Phase.DAY_VOTE, state, round_id,
                prompt_override="本轮投票无人被淘汰（平安日）",
            )

        state.eliminated_today = []

    async def _collect_day_votes(
        self, state: GameState, round_id: str
    ) -> dict[str, str]:
        """Collect elimination votes from all alive players."""
        alive = state.alive_players()
        votes: dict[str, str] = {}

        for player in alive:
            response = await self._call_player(
                player.player_id, player.role, state, "vote",
            )
            target = response.target
            if not (target and target in state.players and state.players[target].is_alive):
                # Fallback: vote for a random alive player (excluding self)
                others = [p for p in alive if p.player_id != player.player_id]
                if others:
                    target = random.choice(others).player_id
            if target and target in state.players and state.players[target].is_alive:
                votes[player.player_id] = target
                self._log_vote(round_id, player.player_id, target, "elimination")
                logger.info("Vote: %s -> %s", player.player_id, target)

        return votes

    async def _run_hunter_death_shot(
        self, state: GameState, hunter_id: str, round_id: str
    ) -> None:
        """Hunter eliminated by vote — gets a death shot."""
        hunter = state.players[hunter_id]
        response = await self._call_player(
            hunter_id, hunter.role, state, "death_shot",
        )
        if response.action == "shoot" and response.target:
            shoot_target = response.target
            resolve_hunter_death(state, hunter_id, shoot_target)
            self._log_event(
                round_id, "hunter_shot", hunter_id,
                {"target": shoot_target},
            )
            logger.info("Hunter %s shot %s before dying", hunter_id, shoot_target)

    async def _handle_sheriff_transfer(
        self, state: GameState, sheriff_id: str, round_id: str
    ) -> None:
        """Handle sheriff badge transfer on death.

        The dying sheriff designates a successor. If the sheriff chooses
        to destroy the badge (撕警徽), no successor is assigned.
        """
        response = await self._call_player(
            sheriff_id, state.players[sheriff_id].role, state, "sheriff_transfer",
        )
        target = response.target
        state.sheriff_id = None
        state.players[sheriff_id].is_sheriff = False

        if target and target in state.players and state.players[target].is_alive:
            state.sheriff_id = target
            state.players[target].is_sheriff = True
            self._log_event(
                round_id, "sheriff_transfer", sheriff_id,
                {"successor": target},
            )
            logger.info("Sheriff badge transferred: %s -> %s", sheriff_id, target)
        else:
            self._log_event(
                round_id, "sheriff_transfer", sheriff_id,
                {"successor": None, "action": "撕警徽"},
            )
            logger.info("Sheriff %s destroyed the badge (撕警徽)", sheriff_id)

    # ── Game end ───────────────────────────────────────────────────

    async def _finalize_game(
        self,
        state: GameState,
        game_id: str,
        winner: str | None,
        reason: str,
    ) -> None:
        """Save game result to DB and announce the winner."""
        winner_str = winner or "stalemate"

        end_round_id = self._create_round(state, Phase.GAME_END)
        await self._narrate(
            Phase.GAME_END, state, end_round_id,
            prompt_override=(
                f"游戏结束，{'狼人阵营' if winner_str == 'werewolf' else '好人阵营' if winner_str == 'good' else '平局'}获胜！"
                f"原因：{reason}"
            ),
        )

        # Reveal all roles
        role_summary = {
            pid: p.role.value for pid, p in state.players.items()
        }
        logger.info("Game %s final roles: %s", game_id, role_summary)

        await self._narrate(
            Phase.GAME_END, state, end_round_id,
            prompt_override=(
                f"身份揭晓："
                + "，".join(
                    f"{p.seat_number}号{p.role.value}"
                    for p in sorted(state.players.values(), key=lambda x: x.seat_number)
                )
            ),
        )

        # Persist result
        self.repo.save_game_result(GameResult(
            game_id=game_id,
            winner=winner_str,
            duration_rounds=state.round_number,
        ))

        self.repo.conn.execute(
            "UPDATE games SET winner = ?, total_rounds = ?, ended_at = ? WHERE id = ?",
            (winner_str, state.round_number, datetime.now().isoformat(), game_id),
        )
        self.repo.conn.commit()
        logger.info("Game %s result saved: winner=%s, rounds=%d", game_id, winner_str, state.round_number)

    # ── Agent call wrappers ────────────────────────────────────────

    async def _call_player(
        self,
        player_id: str,
        role: Role,
        state: GameState,
        action_type: str,
        prompt_override: str | None = None,
        debate_context: str = "",
    ) -> AgentResponse:
        """Call the player agent, returning a default response if agent is None."""
        agent = self.player_agent
        if agent is None:
            return AgentResponse(action="abstain", reasoning="no agent configured")
        try:
            import inspect
            sig = inspect.signature(agent)
            kwargs: dict = {"player_id": player_id, "role": role, "game_state": state, "action_type": action_type}
            if len(sig.parameters) >= 5:
                kwargs["prompt_override"] = prompt_override
            if "debate_context" in sig.parameters:
                kwargs["debate_context"] = debate_context
            return await agent(**kwargs)
        except Exception as e:
            logger.error(
                "Player agent call failed for %s (action=%s): %s",
                player_id, action_type, e,
            )
            return AgentResponse(action="abstain", reasoning=f"agent error: {e}")

    async def _narrate(
        self,
        phase: Phase,
        state: GameState,
        round_id: str,
        prompt_override: str | None = None,
    ) -> None:
        """Call the judge agent for narration, storing the result as an event."""
        agent = self.judge_agent
        if agent is None:
            return
        try:
            response = await agent(phase, state)
            self._log_event(
                round_id, f"judge_narration_{phase.name.lower()}",
                None,
                {
                    "dialogue": response.dialogue,
                    "override": prompt_override,
                },
            )
            if response.dialogue:
                logger.info("Judge [%s]: %s", phase.name, response.dialogue[:120])
        except Exception as e:
            logger.error("Judge agent call failed for phase %s: %s", phase.name, e)

    # ── Persistence helpers ────────────────────────────────────────

    def _persist_players(self, state: GameState, game_id: str) -> None:
        """Create player and game_player records in the DB."""
        for pid, player in state.players.items():
            try:
                self.repo.create_player(PlayerRecord(
                    id=pid,
                    name=f"Player {player.seat_number}",
                ))
            except Exception:
                # Player may already exist
                pass
            self.repo.add_game_player(
                game_id, pid, player.role.value, player.seat_number,
            )

    def _build_round_summary(self, state: GameState, round_id: str) -> str:
        """Build a public round summary for all players to see next round."""
        parts = [f"=== 第{state.round_number}轮 ==="]

        # Night deaths
        if state.eliminated_tonight:
            deaths = [f"{state.players[pid].seat_number}号玩家" for pid in state.eliminated_tonight if pid in state.players]
            parts.append(f"昨夜死亡: {', '.join(deaths)}")
        else:
            parts.append("昨夜是平安夜，无人死亡")

        # Player speeches (public)
        dialogues = self.repo.get_dialogues(round_id)
        if dialogues:
            speech_lines = []
            for d in dialogues:
                if d.content:
                    seat = state.players[d.player_id].seat_number if d.player_id in state.players else d.player_id
                    speech_lines.append(f"  {seat}号: {d.content[:120]}")
            if speech_lines:
                parts.append("发言摘要:\n" + "\n".join(speech_lines))

        # Today's elimination
        if state.eliminated_today:
            for pid in state.eliminated_today:
                if pid in state.players:
                    parts.append(f"今日放逐: {state.players[pid].seat_number}号玩家")

        # Votes
        if state.current_votes:
            vote_tally: dict[str, int] = {}
            for target in state.current_votes.values():
                vote_tally[target] = vote_tally.get(target, 0) + 1
            vote_lines = [f"  {state.players[t].seat_number}号: {c}票" for t, c in sorted(vote_tally.items(), key=lambda x: -x[1]) if t in state.players]
            if vote_lines:
                parts.append("投票:\n" + "\n".join(vote_lines))

        # Sheriff
        if state.sheriff_id and state.sheriff_id in state.players:
            parts.append(f"警长: {state.players[state.sheriff_id].seat_number}号")

        parts.append(f"存活: {state.alive_count()}人")
        return "\n".join(parts)

    def _log_game_start(self, game_id: str) -> None:
        """Log the game start record."""
        self.repo.create_game(GameRecord(
            id=game_id,
            mode="standard",
            winner="",
            total_rounds=0,
            started_at=datetime.now().isoformat(),  # type: ignore[arg-type]
        ))

    def _create_round(self, state: GameState, phase: Phase) -> str:
        """Create a round record in the DB and return its ID."""
        round_id = self._new_id()
        self.repo.create_round(RoundRecord(
            id=round_id,
            game_id=state.game_id,
            round_num=state.round_number,
            phase=phase.value,
        ))
        return round_id

    def _log_event(
        self,
        round_id: str,
        event_type: str,
        player_id: str | None,
        payload: dict | None = None,
    ) -> None:
        """Log a game event to the DB."""
        import json
        self.repo.log_event(EventRecord(
            id=self._new_id(),
            round_id=round_id,
            event_type=event_type,
            player_id=player_id,
            payload=json.dumps(payload, ensure_ascii=False) if payload else None,
        ))

    def _log_dialogue(
        self,
        round_id: str,
        player_id: str,
        content: str,
        reasoning: str,
    ) -> None:
        """Log a player's dialogue to the DB."""
        self.repo.log_dialogue(DialogueRecord(
            id=self._new_id(),
            round_id=round_id,
            player_id=player_id,
            content=content,
            reasoning=reasoning,
        ))

    def _log_vote(
        self,
        round_id: str,
        voter_id: str,
        target_id: str,
        vote_type: str,
    ) -> None:
        """Log a vote to the DB."""
        self.repo.log_vote(VoteRecord(
            id=self._new_id(),
            round_id=round_id,
            voter_id=voter_id,
            target_id=target_id,
            vote_type=vote_type,
        ))

    @staticmethod
    def _new_id() -> str:
        """Generate a unique ID."""
        return str(uuid.uuid4())
