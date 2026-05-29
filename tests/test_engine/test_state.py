"""Tests for GameState helpers and queries."""

import pytest
from werewolf.engine.state import (
    Camp,
    GameState,
    Phase,
    PlayerState,
    Role,
    STANDARD_12P,
    get_camp,
)
from werewolf.engine.phases import setup_phase


def _make_player(player_id: str, seat: int, role: Role, alive: bool = True) -> PlayerState:
    """Helper to create a PlayerState with correct camp derived from role."""
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


class TestAliveQueries:
    """Tests for GameState alive_* query methods."""

    def test_alive_players(self):
        """alive_players should return only players with is_alive=True."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF, alive=True),
            _make_player("p2", 2, Role.VILLAGER, alive=False),
            _make_player("p3", 3, Role.SEER, alive=True),
            _make_player("p4", 4, Role.WITCH, alive=False),
        ]
        state = _make_state(players)
        alive = state.alive_players()
        assert len(alive) == 2, f"Expected 2 alive players, got {len(alive)}"
        alive_ids = {p.player_id for p in alive}
        assert alive_ids == {"p1", "p3"}, f"Unexpected alive set: {alive_ids}"

    def test_alive_werewolves(self):
        """alive_werewolves should return only alive werewolves."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF, alive=True),
            _make_player("p2", 2, Role.WEREWOLF, alive=False),
            _make_player("p3", 3, Role.VILLAGER, alive=True),
        ]
        state = _make_state(players)
        wolves = state.alive_werewolves()
        assert len(wolves) == 1, f"Expected 1 alive werewolf, got {len(wolves)}"
        assert wolves[0].player_id == "p1"

    def test_alive_good_players(self):
        """alive_good_players should return all alive non-werewolf players."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF, alive=True),
            _make_player("p2", 2, Role.VILLAGER, alive=True),
            _make_player("p3", 3, Role.SEER, alive=True),
            _make_player("p4", 4, Role.WITCH, alive=False),
        ]
        state = _make_state(players)
        good = state.alive_good_players()
        assert len(good) == 2, f"Expected 2 alive good players, got {len(good)}"
        good_ids = {p.player_id for p in good}
        assert good_ids == {"p2", "p3"}, f"Unexpected good set: {good_ids}"

    def test_alive_villagers(self):
        """alive_villagers should return alive villagers only."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF, alive=True),
            _make_player("p2", 2, Role.VILLAGER, alive=True),
            _make_player("p3", 3, Role.VILLAGER, alive=False),
            _make_player("p4", 4, Role.SEER, alive=True),
        ]
        state = _make_state(players)
        vills = state.alive_villagers()
        assert len(vills) == 1, f"Expected 1 alive villager, got {len(vills)}"
        assert vills[0].player_id == "p2"

    def test_alive_gods(self):
        """alive_gods should return alive players with special roles (non-villager good)."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF, alive=True),
            _make_player("p2", 2, Role.VILLAGER, alive=True),
            _make_player("p3", 3, Role.SEER, alive=True),
            _make_player("p4", 4, Role.WITCH, alive=True),
            _make_player("p5", 5, Role.HUNTER, alive=True),
            _make_player("p6", 6, Role.GUARD, alive=True),
        ]
        state = _make_state(players)
        gods = state.alive_gods()
        assert len(gods) == 4, f"Expected 4 gods, got {len(gods)}"
        god_ids = {p.player_id for p in gods}
        assert god_ids == {"p3", "p4", "p5", "p6"}, f"Unexpected gods: {god_ids}"

    def test_alive_count(self):
        """alive_count should return the correct number of alive players."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF, alive=True),
            _make_player("p2", 2, Role.VILLAGER, alive=False),
            _make_player("p3", 3, Role.SEER, alive=True),
        ]
        state = _make_state(players)
        assert state.alive_count() == 2, f"Expected 2, got {state.alive_count()}"


class TestSetup:
    """Tests for setup_phase() and related state initialisation."""

    def test_setup_phase_creates_12_players(self):
        """setup_phase should create exactly 12 players."""
        state = GameState(game_id="test-setup", players={})
        state = setup_phase(state)
        assert len(state.players) == 12, f"Expected 12 players, got {len(state.players)}"
        # Check seat numbers 1-12
        for seat in range(1, 13):
            pid = f"player_{seat}"
            assert pid in state.players, f"Missing player {pid}"
            assert state.players[pid].seat_number == seat

    def test_setup_phase_correct_role_counts(self):
        """setup_phase should produce correct role distribution."""
        state = GameState(game_id="test-setup", players={})
        state = setup_phase(state)
        role_counts = {role: 0 for role in Role}
        for p in state.players.values():
            role_counts[p.role] += 1
        assert role_counts[Role.WEREWOLF] == 4, f"Expected 4 werewolves, got {role_counts[Role.WEREWOLF]}"
        assert role_counts[Role.SEER] == 1, f"Expected 1 seer, got {role_counts[Role.SEER]}"
        assert role_counts[Role.WITCH] == 1, f"Expected 1 witch, got {role_counts[Role.WITCH]}"
        assert role_counts[Role.HUNTER] == 1, f"Expected 1 hunter, got {role_counts[Role.HUNTER]}"
        assert role_counts[Role.GUARD] == 1, f"Expected 1 guard, got {role_counts[Role.GUARD]}"
        assert role_counts[Role.VILLAGER] == 4, f"Expected 4 villagers, got {role_counts[Role.VILLAGER]}"


class TestSheriff:
    """Tests for sheriff-related state."""

    def test_sheriff_election_assignment(self):
        """Assigning a sheriff should set is_sheriff flag correctly."""
        players = [
            _make_player("p1", 1, Role.VILLAGER),
            _make_player("p2", 2, Role.WEREWOLF),
        ]
        state = _make_state(players, sheriff_id="p1")
        assert state.sheriff_id == "p1"
        assert state.players["p1"].is_sheriff is True
        assert state.players["p2"].is_sheriff is False


class TestVoteWithSheriff:
    """Tests for vote weight with sheriff."""

    def test_sheriff_vote_tracking(self):
        """Sheriff's vote weight should be properly accounted for in tally."""
        players = [
            _make_player("p1", 1, Role.VILLAGER),
            _make_player("p2", 2, Role.WEREWOLF),
            _make_player("p3", 3, Role.VILLAGER),
            _make_player("p4", 4, Role.WEREWOLF),
        ]
        state = _make_state(players, sheriff_id="p1")

        from werewolf.engine.phases import resolve_vote
        # Sheriff p1 votes for p2, p3 votes for p2, p4 votes for p3
        # p2 gets 1 (p3) + 1.5 (sheriff p1) = 2.5 out of 3 voters
        # This is > 3/2 = 1.5, so p2 should be eliminated
        votes = {"p1": "p2", "p3": "p2", "p4": "p3"}
        state = resolve_vote(state, votes)
        assert "p2" in state.eliminated_today, (
            f"Expected p2 to be eliminated with sheriff bonus, eliminated_today={state.eliminated_today}"
        )


class TestGetCamp:
    """Tests for get_camp utility."""

    def test_werewolf_camp(self):
        assert get_camp(Role.WEREWOLF) == Camp.WEREWOLF

    def test_good_camps(self):
        for role in [Role.SEER, Role.WITCH, Role.HUNTER, Role.GUARD, Role.VILLAGER]:
            assert get_camp(role) == Camp.GOOD, f"{role} should be GOOD camp"
