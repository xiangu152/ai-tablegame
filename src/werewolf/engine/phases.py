"""Game phase implementations for the AI Werewolf game.

This module implements ALL game phase logic as pure state transformations.
It does NOT make any LLM API calls — it only manages state transitions
and collects "agent actions" that need to be taken.

The orchestrator is responsible for calling AI agents and passing their
results to these functions.
"""

from __future__ import annotations

import logging
import random
import uuid

from werewolf.engine.state import (
    Camp,
    GameState,
    Phase,
    PlayerState,
    Role,
    STANDARD_12P,
    get_camp,
)

logger = logging.getLogger(__name__)

# ── Standard night phase order ──────────────────────────────────

STANDARD_NIGHT_ORDER: list[Phase] = [
    Phase.NIGHT_WEREWOLF,
    Phase.NIGHT_SEER,
    Phase.NIGHT_WITCH,
    Phase.NIGHT_HUNTER,
    Phase.NIGHT_GUARD,
]


# ── Setup ───────────────────────────────────────────────────────

def setup_phase(state: GameState) -> GameState:
    """Randomly assign roles to 12 player slots and initialise player states.

    Creates 12 PlayerState objects for seats 1-12 using the STANDARD_12P
    role distribution, then stores them in state.players.
    """
    # Flatten role list from STANDARD_12P counts
    role_pool: list[tuple[Role, Camp]] = []
    for role, count in STANDARD_12P.items():
        camp = get_camp(role)
        role_pool.extend([(role, camp)] * count)

    assert len(role_pool) == 12, f"STANDARD_12P must contain exactly 12 roles, got {len(role_pool)}"

    random.shuffle(role_pool)

    players: dict[str, PlayerState] = {}
    for seat in range(1, 13):
        role, camp = role_pool[seat - 1]
        player_id = _make_player_id(seat)
        players[player_id] = PlayerState(
            player_id=player_id,
            seat_number=seat,
            role=role,
            camp=camp,
        )

    state.players = players
    state.phase = Phase.SETUP
    logger.info(
        "Setup complete: %d players assigned, roles=%s",
        len(players),
        {p.player_id: p.role.value for p in players.values()},
    )
    return state


# ── Sheriff election ────────────────────────────────────────────

def sheriff_election_candidacy(state: GameState) -> list[str]:
    """Return list of player IDs who want to run for sheriff.

    In an AI game every alive player is a candidate — the "退水" (withdraw)
    happens during their campaign speech / LLM reasoning, not via this function.
    """
    return [p.player_id for p in state.alive_players()]


def resolve_sheriff_election(
    state: GameState,
    votes: dict[str, str],
    candidates: list[str] | None = None,
) -> GameState:
    """Resolve sheriff election from collected votes.

    votes: {voter_id: candidate_id}
    candidates: optional list of players still in the race (after 退水).
                Defaults to all alive players (sheriff_election_candidacy).

    - Non-candidates vote; candidates do not vote.
    - Highest-voted candidate becomes sheriff.
    - On tie the caller must handle a re-vote.
    - If a tie persists after re-vote: no sheriff (警徽流失).

    Sets state.sheriff_id and state.sheriff_election_done = True.
    """
    if candidates is None:
        candidates = sheriff_election_candidacy(state)
    candidate_set = set(candidates)

    if not candidate_set:
        state.sheriff_election_done = True
        return state

    # Only non-candidates vote for candidates
    valid_votes: dict[str, str] = {
        voter: target
        for voter, target in votes.items()
        if voter not in candidate_set and target in candidate_set
    }

    if not valid_votes:
        state.sheriff_election_done = True
        return state

    # Count votes per candidate
    tally: dict[str, int] = {}
    for target in valid_votes.values():
        tally[target] = tally.get(target, 0) + 1

    max_votes = max(tally.values())
    winners = [pid for pid, count in tally.items() if count == max_votes]

    if len(winners) == 1:
        winner_id = winners[0]
        state.sheriff_id = winner_id
        state.players[winner_id].is_sheriff = True
        logger.info("Sheriff elected: %s with %d votes", winner_id, max_votes)
    else:
        # Tie — caller must re-vote. Mark election as unresolved for now.
        logger.info("Sheriff election tie between %s (%d votes each)", winners, max_votes)
        state.sheriff_id = None

    state.sheriff_election_done = True
    return state


# ── Night phases ────────────────────────────────────────────────

def night_werewolf_phase(state: GameState, kill_target: str) -> GameState:
    """Record the werewolves' chosen kill target.

    Sets state.night_kill_target.
    """
    if kill_target not in state.players:
        logger.warning("Invalid kill target: %s", kill_target)
        return state
    if not state.players[kill_target].is_alive:
        logger.warning("Kill target %s is already dead", kill_target)
        return state
    state.night_kill_target = kill_target
    logger.info("Werewolves selected kill target: %s", kill_target)
    return state


def night_seer_phase(state: GameState, check_target: str, result: Camp) -> GameState:
    """Record a seer check result.

    Stores the result in state.seer_checks dict.
    """
    if check_target not in state.players:
        logger.warning("Invalid seer check target: %s", check_target)
        return state
    state.seer_checks[check_target] = result
    logger.info("Seer checked %s → %s", check_target, result.value)
    return state


def night_witch_phase(
    state: GameState,
    use_antidote: bool,
    use_poison: bool,
    poison_target: str | None = None,
) -> GameState:
    """Process the witch's night actions.

    - Antidote: marks witch_antidote_used (actual save is resolved later
      to allow 奶穿 detection).
    - Poison: sets witch_poison_target, marks witch_poison_used.
    - 奶穿规则: if both antidote AND guard protect the same target, the target
      dies (handled during resolve_night_deaths).
    """
    if use_antidote and not state.witch_antidote_used:
        state.witch_antidote_used = True
        if state.night_kill_target:
            logger.info(
                "Witch used antidote to save %s",
                state.night_kill_target,
            )

    if use_poison and not state.witch_poison_used:
        if poison_target and poison_target in state.players:
            if not state.players[poison_target].is_alive:
                logger.warning("Poison target %s is already dead", poison_target)
                return state
            state.witch_poison_target = poison_target
            state.witch_poison_used = True
            logger.info("Witch poisoned %s", poison_target)

    return state


def night_guard_phase(state: GameState, protect_target: str) -> GameState:
    """Process the guard's protection action.

    - Cannot protect the same player on two consecutive nights.
    - Sets guard_protect_target and updates guard_last_protect.
    - 奶穿 check: if guard protected the same target the witch antidote-saved,
      the target dies (flag is set for resolve_night_deaths).
    """
    if protect_target not in state.players:
        logger.warning("Invalid guard protect target: %s", protect_target)
        return state

    if not state.players[protect_target].is_alive:
        logger.warning("Guard protect target %s is already dead", protect_target)
        return state

    # Cannot protect the same player twice in a row (unless rules allow — we
    # enforce it here as a soft guard; the orchestrator should also respect it).
    if state.guard_last_protect == protect_target:
        logger.warning(
            "Guard cannot protect %s on consecutive nights — ignoring",
            protect_target,
        )
        return state

    state.guard_protect_target = protect_target
    state.guard_last_protect = protect_target
    logger.info("Guard protected %s", protect_target)
    return state


# ── Night resolution ────────────────────────────────────────────

def resolve_night_deaths(state: GameState) -> GameState:
    """Resolve all deaths that occurred during the night.

    Death causes and protection interactions:
    - Werewolf kill:
      - target dies UNLESS saved by antidote or guard
      - antidote save: witch_antidote_used AND guard did NOT protect the same target
      - guard save: guard protected the kill target AND antidote was NOT used
      - 奶穿 (milk penetration): witch_antidote_used AND guard protected
        the kill target → both protections cancel → target dies
    - Witch poison: target dies unconditionally.

    Updates is_alive statuses. Hunter dying at night loses gun ability
    (cannot retaliate — the hunter's gun only works during daytime
    elimination at night in some rule variants; here: no night shot).

    Clears night state fields after resolution.
    """
    dead_tonight: set[str] = set()
    kill_target = state.night_kill_target
    guard_target = state.guard_protect_target

    # ── Werewolf kill resolution ──
    if kill_target is not None:
        antidote = state.witch_antidote_used
        guarded = guard_target == kill_target

        if antidote and guarded:
            # 奶穿: both protections cancel out → target dies
            dead_tonight.add(kill_target)
            logger.info(
                "奶穿 (milk penetration): antidote+guard on %s → %s dies",
                kill_target,
                kill_target,
            )
        elif antidote and not guarded:
            # Antidote saved the target
            logger.info("Witch antidote saved %s from werewolf kill", kill_target)
        elif not antidote and guarded:
            # Guard saved the target
            logger.info("Guard saved %s from werewolf kill", kill_target)
        else:
            # No protection → target dies
            dead_tonight.add(kill_target)
            logger.info("Werewolves killed %s", kill_target)

    # ── Witch poison ──
    if state.witch_poison_target is not None:
        dead_tonight.add(state.witch_poison_target)
        logger.info("Witch poison killed %s", state.witch_poison_target)

    # Apply deaths
    for pid in dead_tonight:
        if pid in state.players and state.players[pid].is_alive:
            state.players[pid].is_alive = False
            state.eliminated_tonight.append(pid)
            logger.info(
                "Player %s (%s) died during the night",
                pid,
                state.players[pid].role.value,
            )

    # Clear night state
    _clear_night_state(state)

    return state


def _clear_night_state(state: GameState) -> None:
    """Reset all night-phase state fields for the next round."""
    state.night_kill_target = None
    state.witch_poison_target = None
    state.guard_protect_target = None


# ── Day phases ──────────────────────────────────────────────────

def day_discussion_phase(state: GameState) -> list[str]:
    """Return the ordered list of player IDs for the day's speaking order.

    Rules:
    - If sheriff alive: sheriff chooses direction (alternating each round).
      We return clockwise order starting from sheriff +1, or counterclockwise
      starting from sheriff -1, based on state.speaking_direction.
    - If no sheriff but deaths: clockwise from seat 1.
    - If no sheriff and peaceful night (no deaths): clockwise from seat 1.
    """
    alive = sorted(state.alive_players(), key=lambda p: p.seat_number)
    alive_ids = [p.player_id for p in alive]

    if not alive_ids:
        return []

    sheriff = (
        state.players.get(state.sheriff_id)
        if state.sheriff_id
        else None
    )
    has_sheriff = sheriff and sheriff.is_alive

    if has_sheriff:
        assert state.sheriff_id is not None
        sheriff_seat = state.players[state.sheriff_id].seat_number
        direction = state.speaking_direction

        if direction == "clockwise":
            # Start from sheriff+1 clockwise
            order = _clockwise_from(state, sheriff_seat + 1 if sheriff_seat < 12 else 1)
        else:
            # Start from sheriff-1 counterclockwise
            order = _counterclockwise_from(state, sheriff_seat - 1 if sheriff_seat > 1 else 12)

        # Toggle direction for next round
        state.speaking_direction = (
            "counterclockwise" if direction == "clockwise" else "clockwise"
        )
        return order

    # No sheriff: clockwise from seat 1
    return _clockwise_from(state, 1)


def _clockwise_from(state: GameState, start_seat: int) -> list[str]:
    """Return alive player IDs in clockwise order starting from start_seat."""
    all_seats = sorted(state.players.values(), key=lambda p: p.seat_number)
    ordered = all_seats[start_seat - 1 :] + all_seats[: start_seat - 1]
    return [p.player_id for p in ordered if p.is_alive]


def _counterclockwise_from(state: GameState, start_seat: int) -> list[str]:
    """Return alive player IDs in counterclockwise order starting from start_seat."""
    all_seats = sorted(state.players.values(), key=lambda p: p.seat_number)
    # Reverse order
    reversed_order = list(reversed(all_seats))
    # Find the index of start_seat in reversed_order
    for i, p in enumerate(reversed_order):
        if p.seat_number == start_seat:
            ordered = reversed_order[i:] + reversed_order[:i]
            return [p.player_id for p in ordered if p.is_alive]
    # Fallback
    return [p.player_id for p in reversed_order if p.is_alive]


# ── Vote resolution ─────────────────────────────────────────────

def resolve_vote(state: GameState, votes: dict[str, str]) -> GameState:
    """Resolve a day elimination vote.

    votes: {voter_id: target_id}

    - Sheriff's vote counts as 1.5 (add 0.5 for tie-breaking).
    - Track re-vote attempts (max 2).
    - On majority (>50%): eliminate target.
    - Hunter eliminated → gun fires → hunter selects target to take down.
    - On tie after 2 re-votes: no elimination (平安日).
    - Sheriff death → badge transfer to chosen successor or撕毁 (handled by caller).
    """
    alive = {p.player_id for p in state.alive_players() if p.can_vote}

    # Filter votes to only valid (alive, can_vote) voters
    valid: dict[str, str] = {}
    for voter, target in votes.items():
        if voter not in alive:
            continue
        if target not in state.players:
            continue
        if not state.players[target].is_alive:
            continue
        valid[voter] = target

    if not valid:
        state.vote_round += 1
        return state

    # Count votes with sheriff bonus
    tally: dict[str, float] = {}
    for target in valid.values():
        tally[target] = tally.get(target, 0) + 1

    # Sheriff bonus: +0.5 to the target the sheriff voted for
    sheriff_bonus_applied = False
    if state.sheriff_id and state.players[state.sheriff_id].is_alive:
        sheriff_vote = valid.get(state.sheriff_id)
        if sheriff_vote:
            tally[sheriff_vote] = tally.get(sheriff_vote, 0) + 0.5
            sheriff_bonus_applied = True

    max_votes = max(tally.values())
    winners = [pid for pid, count in tally.items() if count == max_votes]

    total_voters = len(valid)
    # Majority: for integer counts, majority is > total/2.
    # With sheriff bonus, use > total_voters / 2.
    majority_threshold = total_voters / 2.0

    if len(winners) == 1 and max_votes > majority_threshold:
        # Single winner with majority → eliminate
        eliminated_id = winners[0]
        _eliminate(state, eliminated_id)
        state.vote_round = 0
    elif len(winners) >= 2:
        # Tie: check re-vote attempts
        state.vote_round += 1
        if state.vote_round >= 3:
            # After 2 re-votes (vote_round reaches 3), no elimination
            logger.info("Vote tied after 2 re-votes — no elimination (平安日)")
            state.vote_round = 3  # signal orchestrator loop to break
        else:
            logger.info("Vote tied between %s — waiting for re-vote (attempt %d)", winners, state.vote_round)
    else:
        # Single winner but no majority OR no majority reached
        state.vote_round += 1
        if state.vote_round >= 3:
            logger.info("No majority after 2 re-votes — no elimination (平安日)")
            state.vote_round = 3  # signal orchestrator loop to break

    return state


def _eliminate(state: GameState, player_id: str) -> None:
    """Mark a player as eliminated and handle role-specific effects.

    - Hunter: gun fires (marked for the caller to handle).
    - Sheriff: badge transfer (caller handles).
    """
    player = state.players.get(player_id)
    if not player or not player.is_alive:
        return

    player.is_alive = False
    state.eliminated_today.append(player_id)
    logger.info("Player %s (%s) eliminated by vote", player_id, player.role.value)


# ── Hunter death ────────────────────────────────────────────────

def resolve_hunter_death(state: GameState, hunter_id: str, shoot_target: str) -> GameState:
    """Resolve a hunter's death: hunter takes the shoot_target down with them.

    The hunter's gun is disabled after use.
    """
    hunter = state.players.get(hunter_id)
    if not hunter or hunter.role != Role.HUNTER:
        logger.warning("resolve_hunter_death: %s is not a hunter", hunter_id)
        return state

    target = state.players.get(shoot_target)
    if not target or not target.is_alive:
        logger.warning("Hunter shoot target %s is invalid or already dead", shoot_target)
        return state

    target.is_alive = False
    logger.info(
        "Hunter %s took down %s (%s) before dying",
        hunter_id,
        shoot_target,
        target.role.value,
    )
    return state


# ── Wolf self-destruct ──────────────────────────────────────────

def wolf_self_destruct(
    state: GameState, wolf_id: str, during_election: bool = False
) -> GameState:
    """A werewolf reveals their identity and dies immediately (自爆).

    - during_election=True: badge lost (警徽流失), skip to night phase.
    - Otherwise: skip day discussion, go directly to night.
    """
    wolf = state.players.get(wolf_id)
    if not wolf or wolf.role != Role.WEREWOLF:
        logger.warning("wolf_self_destruct: %s is not a werewolf", wolf_id)
        return state
    if not wolf.is_alive:
        logger.warning("wolf_self_destruct: %s is already dead", wolf_id)
        return state

    wolf.is_alive = False
    logger.info("Werewolf %s self-destructed (自爆)", wolf_id)

    if during_election:
        # 警徽流失 — no sheriff is elected, go directly to night
        state.sheriff_id = None
        state.sheriff_election_done = True
        state.phase = Phase.NIGHT_WEREWOLF
        logger.info("Wolf self-destructed during election — badge lost, entering night")
    else:
        # Skip day discussion, go to night
        state.eliminated_today.append(wolf_id)
        state.phase = Phase.NIGHT_WEREWOLF
        logger.info("Wolf self-destructed — skipping to night phase")

    return state


# ── Helpers ──────────────────────────────────────────────────────

def _make_player_id(seat_number: int) -> str:
    """Generate a deterministic human-readable player ID from seat number."""
    return f"player_{seat_number}"
