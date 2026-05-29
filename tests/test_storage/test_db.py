"""Tests for Repository CRUD operations."""

import pytest
from datetime import datetime
from werewolf.storage.repository import Repository
from werewolf.storage.models import (
    GameRecord,
    PlayerRecord,
    GamePlayer,
    RoundRecord,
    EventRecord,
    DialogueRecord,
    VoteRecord,
    GameResult,
)


@pytest.fixture
def repo(tmp_path):
    """Create a temporary SQLite-based Repository."""
    db_path = str(tmp_path / "test_db.db")
    return Repository(db_path)


@pytest.fixture
def now():
    """Current timestamp for tests."""
    return datetime.now().isoformat()


class TestGameCRUD:
    """Tests for game record CRUD operations."""

    def test_create_game(self, repo, now):
        """Creating a game should insert it into the DB."""
        game_id = repo.create_game(GameRecord(
            id="g-1",
            mode="standard",
            winner="",
            total_rounds=0,
            started_at=now,
        ))
        assert game_id == "g-1"

        game = repo.get_game("g-1")
        assert game is not None
        assert game.mode == "standard"
        assert game.total_rounds == 0

    def test_get_game_nonexistent(self, repo):
        """Getting a non-existent game should return None."""
        game = repo.get_game("nonexistent")
        assert game is None

    def test_get_all_games(self, repo, now):
        """get_all_games should return all games sorted by started_at DESC."""
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at="2024-01-01"))
        repo.create_game(GameRecord(id="g-2", mode="std", winner="", total_rounds=0, started_at="2024-06-01"))
        repo.create_game(GameRecord(id="g-3", mode="std", winner="", total_rounds=0, started_at="2024-03-01"))

        games = repo.get_all_games()
        assert len(games) == 3
        # Most recent first
        assert games[0].id == "g-2"
        assert games[1].id == "g-3"
        assert games[2].id == "g-1"

    def test_get_game_count(self, repo, now):
        """get_game_count should return correct count."""
        assert repo.get_game_count() == 0
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at=now))
        assert repo.get_game_count() == 1
        repo.create_game(GameRecord(id="g-2", mode="std", winner="", total_rounds=0, started_at=now))
        assert repo.get_game_count() == 2


class TestPlayerCRUD:
    """Tests for player record CRUD operations."""

    def test_create_player(self, repo):
        """Creating a player should insert it into the DB."""
        player_id = repo.create_player(PlayerRecord(id="p-1", name="Test Player"))
        assert player_id == "p-1"

    def test_add_game_player(self, repo, now):
        """Adding a game_player should link player to game with role and seat."""
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at=now))
        repo.create_player(PlayerRecord(id="p-1", name="Player 1"))
        repo.add_game_player("g-1", "p-1", "werewolf", 1)

        players = repo.get_game_players("g-1")
        assert len(players) == 1
        assert players[0][0] == "p-1"
        assert players[0][1] == "werewolf"
        assert players[0][2] == 1

    def test_add_multiple_game_players(self, repo, now):
        """Multiple players can be added to a game."""
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at=now))
        for i in range(1, 6):
            repo.create_player(PlayerRecord(id=f"p-{i}", name=f"Player {i}"))
            repo.add_game_player("g-1", f"p-{i}", "villager", i)

        players = repo.get_game_players("g-1")
        assert len(players) == 5


class TestRoundCRUD:
    """Tests for round record CRUD operations."""

    def test_create_round(self, repo, now):
        """Creating a round should insert it with FK to game."""
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at=now))
        round_id = repo.create_round(RoundRecord(
            id="r-1",
            game_id="g-1",
            round_num=1,
            phase="night_werewolf",
        ))
        assert round_id == "r-1"

        rounds = repo.get_rounds("g-1")
        assert len(rounds) == 1
        assert rounds[0].round_num == 1
        assert rounds[0].phase == "night_werewolf"

    def test_get_rounds_empty(self, repo, now):
        """Getting rounds for a game with no rounds should return empty list."""
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at=now))
        rounds = repo.get_rounds("g-1")
        assert rounds == []

    def test_get_rounds_ordered(self, repo, now):
        """Rounds should be returned in round_num order."""
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at=now))
        repo.create_round(RoundRecord(id="r-2", game_id="g-1", round_num=2, phase="day_vote"))
        repo.create_round(RoundRecord(id="r-1", game_id="g-1", round_num=1, phase="night_werewolf"))
        repo.create_round(RoundRecord(id="r-3", game_id="g-1", round_num=3, phase="game_end"))

        rounds = repo.get_rounds("g-1")
        assert len(rounds) == 3
        assert rounds[0].round_num == 1
        assert rounds[1].round_num == 2
        assert rounds[2].round_num == 3


class TestEventLog:
    """Tests for event logging."""

    def test_log_event(self, repo, now):
        """Logging an event should store it correctly."""
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at=now))
        repo.create_round(RoundRecord(id="r-1", game_id="g-1", round_num=1, phase="night_werewolf"))
        event_id = repo.log_event(EventRecord(
            id="e-1",
            round_id="r-1",
            event_type="night_kill",
            player_id="p-1",
            payload='{"target": "player_5"}',
        ))
        assert event_id == "e-1"

        events = repo.get_events("r-1")
        assert len(events) == 1
        assert events[0].event_type == "night_kill"
        assert events[0].player_id == "p-1"

    def test_get_events_empty(self, repo, now):
        """Getting events for a round with no events should return empty list."""
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at=now))
        repo.create_round(RoundRecord(id="r-1", game_id="g-1", round_num=1, phase="setup"))
        events = repo.get_events("r-1")
        assert events == []


class TestDialogueLog:
    """Tests for dialogue logging."""

    def test_log_dialogue(self, repo, now):
        """Logging a dialogue should store it correctly."""
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at=now))
        repo.create_round(RoundRecord(id="r-1", game_id="g-1", round_num=1, phase="day_discussion"))
        dialogue_id = repo.log_dialogue(DialogueRecord(
            id="d-1",
            round_id="r-1",
            player_id="p-1",
            content="I think player 2 is suspicious",
            reasoning="They were quiet during the night",
        ))
        assert dialogue_id == "d-1"

        dialogues = repo.get_dialogues("r-1")
        assert len(dialogues) == 1
        assert dialogues[0].content == "I think player 2 is suspicious"
        assert "quiet" in dialogues[0].reasoning


class TestVoteLog:
    """Tests for vote logging."""

    def test_log_vote(self, repo, now):
        """Logging a vote should store it correctly."""
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at=now))
        repo.create_round(RoundRecord(id="r-1", game_id="g-1", round_num=1, phase="day_vote"))
        vote_id = repo.log_vote(VoteRecord(
            id="v-1",
            round_id="r-1",
            voter_id="p-1",
            target_id="p-2",
            vote_type="elimination",
        ))
        assert vote_id == "v-1"

        votes = repo.get_votes("r-1")
        assert len(votes) == 1
        assert votes[0].voter_id == "p-1"
        assert votes[0].target_id == "p-2"
        assert votes[0].vote_type == "elimination"


class TestGameResult:
    """Tests for game result persistence."""

    def test_save_game_result(self, repo, now):
        """Saving a game result should store it correctly."""
        repo.create_game(GameRecord(id="g-1", mode="std", winner="", total_rounds=0, started_at=now))
        repo.save_game_result(GameResult(
            game_id="g-1",
            winner="werewolf",
            duration_rounds=8,
            mvp_player_id="p-3",
            analysis_report="Good game",
        ))
        # Verify it was saved by querying directly
        row = repo.conn.execute(
            "SELECT * FROM game_results WHERE game_id = ?", ("g-1",)
        ).fetchone()
        assert row is not None
        assert row["winner"] == "werewolf"
        assert row["duration_rounds"] == 8
        assert row["mvp_player_id"] == "p-3"


class TestPerRoleWinRates:
    """Tests for win rate statistics."""

    def test_get_per_role_win_rates(self, repo, now):
        """get_per_role_win_rates should return role-based win statistics."""
        # Create test data
        for gid in ["g-1", "g-2", "g-3"]:
            repo.create_game(GameRecord(id=gid, mode="std", winner="werewolf", total_rounds=0, started_at=now))

        # Add players to games
        repo.create_player(PlayerRecord(id="p-1", name="P1"))
        repo.add_game_player("g-1", "p-1", "werewolf", 1)
        repo.add_game_player("g-2", "p-1", "werewolf", 1)
        repo.add_game_player("g-3", "p-1", "seer", 1)

        stats = repo.get_per_role_win_rates()
        assert len(stats) > 0, f"Expected stats, got empty list"
        # At minimum, we should get stats rows back
        assert all("role" in row for row in stats)
        assert all("total_games" in row for row in stats)


class TestRepositoryClose:
    """Test that the Repository can be properly closed."""

    def test_close(self, repo):
        """Closing a repo should not raise."""
        repo.close()
        # Closing is idempotent
        repo.close()
