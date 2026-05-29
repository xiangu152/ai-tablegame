"""Tests for RulesEngine victory condition checker."""

import pytest
from werewolf.engine.state import Camp, GameState, Phase, PlayerState, Role
from werewolf.engine.rules import RulesEngine


def _make_player(player_id: str, seat: int, role: Role, alive: bool = True) -> PlayerState:
    """Helper to create a PlayerState with the correct camp derived from role."""
    camp = Camp.WEREWOLF if role == Role.WEREWOLF else Camp.GOOD
    return PlayerState(
        player_id=player_id,
        seat_number=seat,
        role=role,
        camp=camp,
        is_alive=alive,
    )


def _state_with_players(players: list[PlayerState], round_number: int = 5) -> GameState:
    """Create a GameState pre-populated with the given players."""
    state = GameState(
        game_id="test-game",
        players={p.player_id: p for p in players},
        round_number=round_number,
    )
    return state


class TestCheckVictory:
    """Tests for RulesEngine.check_victory()."""

    def test_no_winner_in_progress(self):
        """Game in progress with mixed alive roles should return no winner."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF),
            _make_player("p2", 2, Role.WEREWOLF),
            _make_player("p3", 3, Role.VILLAGER),
            _make_player("p4", 4, Role.VILLAGER),
            _make_player("p5", 5, Role.SEER),
        ]
        state = _state_with_players(players)
        winner, reason = RulesEngine.check_victory(state)
        assert winner is None, f"Expected no winner, got {winner}"
        assert "progress" in reason.lower()

    def test_good_win_all_werewolves_dead(self):
        """When all werewolves are dead, good team wins."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF, alive=False),
            _make_player("p2", 2, Role.VILLAGER),
            _make_player("p3", 3, Role.SEER),
        ]
        state = _state_with_players(players)
        winner, reason = RulesEngine.check_victory(state)
        assert winner == "good", f"Expected 'good', got {winner}"
        assert "eliminated" in reason.lower()

    def test_werewolf_win_all_villagers_dead(self):
        """When all villagers are dead, werewolves win (屠边-平民)."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF),
            _make_player("p4", 2, Role.WEREWOLF),
            _make_player("p6", 3, Role.VILLAGER, alive=False),
            _make_player("p7", 4, Role.VILLAGER, alive=False),
            _make_player("p8", 5, Role.SEER),
        ]
        state = _state_with_players(players)
        winner, reason = RulesEngine.check_victory(state)
        assert winner == "werewolf", f"Expected 'werewolf', got {winner}"
        assert "villager" in reason.lower()

    def test_werewolf_win_all_gods_dead(self):
        """When all gods are dead, werewolves win (屠边-神民)."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF),
            _make_player("p4", 2, Role.WEREWOLF),
            _make_player("p2", 3, Role.SEER, alive=False),
            _make_player("p3", 4, Role.WITCH, alive=False),
            _make_player("p5", 5, Role.HUNTER, alive=False),
            _make_player("p9", 6, Role.GUARD, alive=False),
            _make_player("p6", 7, Role.VILLAGER),
            _make_player("p7", 8, Role.VILLAGER),
        ]
        state = _state_with_players(players)
        winner, reason = RulesEngine.check_victory(state)
        assert winner == "werewolf", f"Expected 'werewolf', got {winner}"
        assert "god" in reason.lower()

    def test_werewolf_win_parity(self):
        """2 werewolves, 2 good alive should result in werewolf win via parity."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF),
            _make_player("p2", 2, Role.WEREWOLF),
            _make_player("p3", 3, Role.VILLAGER),
            _make_player("p4", 4, Role.SEER),
        ]
        state = _state_with_players(players)
        winner, reason = RulesEngine.check_victory(state)
        assert winner == "werewolf", f"Expected 'werewolf', got {winner}"
        assert "parity" in reason.lower()

    def test_stalemate_round_limit(self):
        """When round_number exceeds limit, game should end in stalemate."""
        players = [
            _make_player("p1", 1, Role.WEREWOLF),
            _make_player("p2", 2, Role.VILLAGER),
        ]
        state = _state_with_players(players, round_number=21)
        winner, reason = RulesEngine.check_victory(state, round_limit=20)
        assert winner is None, f"Expected None (stalemate), got {winner}"
        assert "stalemate" in reason.lower()
