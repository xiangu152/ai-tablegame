"""Tests for game phase functions in phases.py."""

import pytest
from werewolf.engine.state import (
    Camp,
    GameState,
    Phase,
    PlayerState,
    Role,
)
from werewolf.engine.phases import (
    setup_phase,
    resolve_sheriff_election,
    night_werewolf_phase,
    night_seer_phase,
    night_witch_phase,
    night_guard_phase,
    resolve_night_deaths,
    resolve_vote,
    resolve_hunter_death,
    wolf_self_destruct,
    day_discussion_phase,
    STANDARD_NIGHT_ORDER,
)


def _make_player(player_id: str, seat: int, role: Role, alive: bool = True) -> PlayerState:
    """Helper to create a PlayerState with the correct camp."""
    camp = Camp.WEREWOLF if role == Role.WEREWOLF else Camp.GOOD
    return PlayerState(
        player_id=player_id,
        seat_number=seat,
        role=role,
        camp=camp,
        is_alive=alive,
    )


def _make_state(players: list[PlayerState], sheriff_id: str | None = None) -> GameState:
    """Create a GameState with the given players."""
    state = GameState(
        game_id="test-game",
        players={p.player_id: p for p in players},
    )
    if sheriff_id:
        state.sheriff_id = sheriff_id
        if sheriff_id in state.players:
            state.players[sheriff_id].is_sheriff = True
    return state


class TestSetupPhase:
    """Tests for setup_phase()."""

    def test_setup_phase_player_count(self):
        """setup_phase should create 12 players."""
        state = GameState(game_id="test-setup", players={})
        state = setup_phase(state)
        assert len(state.players) == 12

    def test_setup_phase_role_distribution(self):
        """setup_phase should assign exactly 4 werewolves, 1 of each god, 4 villagers."""
        state = GameState(game_id="test-setup", players={})
        state = setup_phase(state)
        role_counts = {role: 0 for role in Role}
        for p in state.players.values():
            role_counts[p.role] += 1
        assert role_counts[Role.WEREWOLF] == 4
        assert role_counts[Role.SEER] == 1
        assert role_counts[Role.WITCH] == 1
        assert role_counts[Role.HUNTER] == 1
        assert role_counts[Role.GUARD] == 1
        assert role_counts[Role.VILLAGER] == 4


class TestSheriffElection:
    """Tests for resolve_sheriff_election()."""

    def test_sheriff_election_basic_win(self):
        """Candidate with the most votes should become sheriff."""
        players = [
            _make_player("p1", 1, Role.VILLAGER),
            _make_player("p2", 2, Role.WEREWOLF),
            _make_player("p3", 3, Role.VILLAGER),
            _make_player("p4", 4, Role.WEREWOLF),
        ]
        state = _make_state(players)
        candidates = ["p1", "p2"]
        # Non-candidates p3, p4 vote: both for p1
        votes = {"p3": "p1", "p4": "p1"}
        state = resolve_sheriff_election(state, votes, candidates)
        assert state.sheriff_id == "p1", f"Expected p1 as sheriff, got {state.sheriff_id}"
        assert state.players["p1"].is_sheriff is True
        assert state.sheriff_election_done is True

    def test_sheriff_election_tie_then_resolve(self):
        """Tie in sheriff election should result in no sheriff (caller re-votes)."""
        players = [
            _make_player("p1", 1, Role.VILLAGER),
            _make_player("p2", 2, Role.WEREWOLF),
            _make_player("p3", 3, Role.VILLAGER),
            _make_player("p4", 4, Role.WEREWOLF),
        ]
        state = _make_state(players)
        candidates = ["p1", "p2"]
        # Non-candidates p3, p4 split votes
        votes = {"p3": "p1", "p4": "p2"}
        state = resolve_sheriff_election(state, votes, candidates)
        assert state.sheriff_id is None, f"Expected no sheriff on tie, got {state.sheriff_id}"

    def test_sheriff_election_double_tie_no_sheriff(self):
        """Two attempts at resolving tie: second call with same votes still ties."""
        players = [
            _make_player("p1", 1, Role.VILLAGER),
            _make_player("p2", 2, Role.WEREWOLF),
            _make_player("p3", 3, Role.VILLAGER),
            _make_player("p4", 4, Role.WEREWOLF),
        ]
        state = _make_state(players)
        candidates = ["p1", "p2"]
        votes = {"p3": "p1", "p4": "p2"}
        state = resolve_sheriff_election(state, votes, candidates)
        # Try re-vote: new state, same result
        state.sheriff_election_done = False  # simulates re-vote
        state = resolve_sheriff_election(state, votes, candidates)
        assert state.sheriff_id is None, "Expected no sheriff after double tie"

    def test_sheriff_election_no_candidates(self):
        """No candidates should result in no sheriff."""
        players = [
            _make_player("p1", 1, Role.VILLAGER),
            _make_player("p2", 2, Role.WEREWOLF),
        ]
        state = _make_state(players)
        state = resolve_sheriff_election(state, {}, candidates=[])
        assert state.sheriff_id is None
        assert state.sheriff_election_done is True


class TestNightPhases:
    """Tests for individual night phase functions."""

    def test_night_werewolf_kill(self):
        """Werewolf selects a kill target."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF),
            _make_player("p2", 2, Role.VILLAGER),
        ]
        state = _make_state(players)
        state = night_werewolf_phase(state, "p2")
        assert state.night_kill_target == "p2"

    def test_night_seer_check(self):
        """Seer checks a player and result is recorded."""
        players = [
            _make_player("p1", 1, Role.SEER),
            _make_player("p2", 2, Role.WEREWOLF),
        ]
        state = _make_state(players)
        state = night_seer_phase(state, "p2", Camp.WEREWOLF)
        assert "p2" in state.seer_checks
        assert state.seer_checks["p2"] == Camp.WEREWOLF

    def test_night_witch_antidote(self):
        """Witch uses antidote to save kill target."""
        players = [
            _make_player("p1", 1, Role.WITCH),
            _make_player("p2", 2, Role.VILLAGER),
        ]
        state = _make_state(players)
        state.night_kill_target = "p2"
        state = night_witch_phase(state, use_antidote=True, use_poison=False)
        assert state.witch_antidote_used is True

    def test_night_witch_poison(self):
        """Witch poisons a target."""
        players = [
            _make_player("p1", 1, Role.WITCH),
            _make_player("p2", 2, Role.WEREWOLF),
        ]
        state = _make_state(players)
        state = night_witch_phase(state, use_antidote=False, use_poison=True, poison_target="p2")
        assert state.witch_poison_used is True
        assert state.witch_poison_target == "p2"

    def test_night_guard_protect(self):
        """Guard protects a target."""
        players = [
            _make_player("p1", 1, Role.GUARD),
            _make_player("p2", 2, Role.VILLAGER),
        ]
        state = _make_state(players)
        state = night_guard_phase(state, "p2")
        assert state.guard_protect_target == "p2"
        assert state.guard_last_protect == "p2"

    def test_guard_cannot_protect_same_twice(self):
        """Guard cannot protect the same player on consecutive nights."""
        players = [
            _make_player("p1", 1, Role.GUARD),
            _make_player("p2", 2, Role.VILLAGER),
            _make_player("p3", 3, Role.WEREWOLF),
        ]
        state = _make_state(players)
        state.guard_last_protect = "p2"
        state = night_guard_phase(state, "p2")
        # Should be ignored, protect target not updated
        assert state.guard_protect_target != "p2", "Guard should not be able to protect same player twice in a row"


class TestNaichuan:
    """Tests for 奶穿 (milk penetration) interaction."""

    def test_naichuan_kill_target_dies(self):
        """When guard and antidote both save same target, target still dies (奶穿)."""
        players = [
            _make_player("p1", 1, Role.WITCH),
            _make_player("p2", 2, Role.GUARD),
            _make_player("p3", 3, Role.VILLAGER),
        ]
        state = _make_state(players)
        state.night_kill_target = "p3"
        state.witch_antidote_used = True
        state.guard_protect_target = "p3"
        state = resolve_night_deaths(state)
        assert state.players["p3"].is_alive is False, (
            "Player should die from 奶穿 (both protections cancel)"
        )


class TestNightResolution:
    """Tests for resolve_night_deaths()."""

    def test_resolve_werewolf_kill_no_protection(self):
        """Kill target dies with no protection."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF),
            _make_player("p2", 2, Role.VILLAGER),
        ]
        state = _make_state(players)
        state.night_kill_target = "p2"
        state = resolve_night_deaths(state)
        assert state.players["p2"].is_alive is False

    def test_resolve_guard_save(self):
        """Guard protection saves the kill target."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF),
            _make_player("p2", 2, Role.GUARD),
            _make_player("p3", 3, Role.VILLAGER),
        ]
        state = _make_state(players)
        state.night_kill_target = "p3"
        state.guard_protect_target = "p3"
        state = resolve_night_deaths(state)
        assert state.players["p3"].is_alive is True, "Guard should save target from werewolf kill"

    def test_resolve_antidote_save(self):
        """Witch antidote saves the kill target."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF),
            _make_player("p2", 2, Role.WITCH),
            _make_player("p3", 3, Role.VILLAGER),
        ]
        state = _make_state(players)
        state.night_kill_target = "p3"
        state.witch_antidote_used = True
        state = resolve_night_deaths(state)
        assert state.players["p3"].is_alive is True, "Witch antidote should save target"

    def test_resolve_poison_death(self):
        """Witch poison kills target unconditionally."""
        players = [
            _make_player("p1", 1, Role.WITCH),
            _make_player("p2", 2, Role.VILLAGER),
        ]
        state = _make_state(players)
        state.witch_poison_target = "p2"
        state = resolve_night_deaths(state)
        assert state.players["p2"].is_alive is False

    def test_resolve_multiple_deaths(self):
        """Multiple death sources should all be applied."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF),
            _make_player("p2", 2, Role.WITCH),
            _make_player("p3", 3, Role.VILLAGER),  # kill target
            _make_player("p4", 4, Role.VILLAGER),  # poison target
        ]
        state = _make_state(players)
        state.night_kill_target = "p3"
        state.witch_poison_target = "p4"
        state = resolve_night_deaths(state)
        assert state.players["p3"].is_alive is False, "Kill target should die"
        assert state.players["p4"].is_alive is False, "Poison target should die"


class TestDayVote:
    """Tests for resolve_vote()."""

    def test_resolve_vote_majority(self):
        """Player with majority vote is eliminated."""
        players = [
            _make_player("p1", 1, Role.VILLAGER),
            _make_player("p2", 2, Role.WEREWOLF),
            _make_player("p3", 3, Role.VILLAGER),
            _make_player("p4", 4, Role.WEREWOLF),
        ]
        state = _make_state(players)
        # p4 gets 2 votes from p1, p3; p2 gets 1 from p4
        votes = {"p1": "p4", "p3": "p4", "p4": "p2"}
        state = resolve_vote(state, votes)
        assert "p4" in state.eliminated_today, f"Expected p4 eliminated, got {state.eliminated_today}"

    def test_resolve_vote_tie_no_elimination(self):
        """Tie results in no elimination if vote_round exhausted."""
        players = [
            _make_player("p1", 1, Role.VILLAGER),
            _make_player("p2", 2, Role.WEREWOLF),
            _make_player("p3", 3, Role.VILLAGER),
            _make_player("p4", 4, Role.WEREWOLF),
        ]
        state = _make_state(players)
        state.vote_round = 2  # Already at max re-votes
        votes = {"p1": "p2", "p2": "p1"}
        state = resolve_vote(state, votes)
        assert state.eliminated_today == [], "No elimination on tie after max re-votes"


class TestHunterDeath:
    """Tests for resolve_hunter_death()."""

    def test_hunter_shoots_on_death(self):
        """Hunter can take a target down when dying."""
        players = [
            _make_player("p1", 1, Role.HUNTER),
            _make_player("p2", 2, Role.WEREWOLF),
            _make_player("p3", 3, Role.VILLAGER),
        ]
        state = _make_state(players)
        state = resolve_hunter_death(state, "p1", "p2")
        assert state.players["p2"].is_alive is False, "Hunter should kill target"


class TestWolfSelfDestruct:
    """Tests for wolf_self_destruct()."""

    def test_wolf_self_destruct(self):
        """Werewolf self-destructs and dies immediately."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF),
            _make_player("p2", 2, Role.VILLAGER),
        ]
        state = _make_state(players)
        state = wolf_self_destruct(state, "p1", during_election=False)
        assert state.players["p1"].is_alive is False, "Wolf should be dead"
        assert state.phase == Phase.NIGHT_WEREWOLF, "Should skip to night phase"


class TestDayDiscussion:
    """Tests for day_discussion_phase()."""

    def test_speaking_order_no_sheriff(self):
        """Without sheriff, speaking order should be clockwise from seat 1."""
        players = [
            _make_player("p1", 1, Role.VILLAGER),
            _make_player("p2", 2, Role.WEREWOLF),
            _make_player("p3", 3, Role.VILLAGER),
        ]
        state = _make_state(players)
        order = day_discussion_phase(state)
        assert order == ["p1", "p2", "p3"], f"Unexpected order: {order}"

    def test_speaking_order_with_sheriff(self):
        """With sheriff, speaking order starts from sheriff+1 clockwise."""
        players = [
            _make_player("p1", 1, Role.VILLAGER),
            _make_player("p2", 2, Role.WEREWOLF),
            _make_player("p3", 3, Role.VILLAGER),
        ]
        state = _make_state(players, sheriff_id="p1")
        state.speaking_direction = "clockwise"
        order = day_discussion_phase(state)
        # Sheriff p1 speaks last; starts from p2 (seat 2)
        assert order[0] == "p2", f"Should start from p2 (seat 2), got {order}"
        assert order[-1] == "p1", f"Sheriff should speak last, got {order}"
