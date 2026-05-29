"""Tests for MemoryBank and Memory classes."""

import pytest
from werewolf.learning.memory import Memory, MemoryBank
from werewolf.storage.repository import Repository


@pytest.fixture
def repo(tmp_path):
    """Create a temporary SQLite Repository for MemoryBank with base game records."""
    import datetime
    from werewolf.storage.models import GameRecord
    db_path = str(tmp_path / "test_memory.db")
    r = Repository(db_path)
    now = datetime.datetime.now()
    for gid in ["game-base", "game-1", "g1", "g2", "g3"]:
        r.create_game(GameRecord(
            id=gid, mode="standard", winner=None,
            total_rounds=0, started_at=now, ended_at=None,
        ))
    return r


@pytest.fixture
def bank(repo):
    """Create a MemoryBank with a temp repo."""
    return MemoryBank(repo)


class TestStoreMemory:
    """Tests for MemoryBank.store()."""

    def test_store_memory_returns_id(self, bank):
        """Storing a memory should return a valid integer ID."""
        mem_id = bank.store(
            game_id="game-1",
            player_role="werewolf",
            situation="First night, no information available",
            decision="Kill player 5",
            outcome="Player 5 was the seer, good elimination",
            lesson="Target players who talk too much",
            quality_score=0.8,
            situation_keywords="night,first,kill",
        )
        assert mem_id > 0, f"Expected positive memory ID, got {mem_id}"

    def test_store_memory_persists_across_instances(self, repo, tmp_path):
        """Memory stored via one bank should be retrievable via another."""
        bank1 = MemoryBank(repo)
        bank1.store(
            game_id="game-1",
            player_role="seer",
            situation="Night 1 check",
            decision="Check player 2",
            outcome="Player 2 was werewolf",
            lesson="Check suspicious players",
            quality_score=0.9,
            situation_keywords="check,first,suspicious",
        )
        # Create a new bank with the same repo
        bank2 = MemoryBank(repo)
        results = bank2.retrieve("seer", k=5)
        assert len(results) > 0, "Memory should persist across instances"
        assert results[0].player_role == "seer"


class TestRetrieveByRole:
    """Tests for MemoryBank.retrieve() role filtering."""

    def test_retrieve_by_role(self, bank):
        """Retrieval should filter by player_role."""
        bank.store(game_id="g1", player_role="werewolf", situation="s1", decision="d1",
                   outcome="o1", lesson="l1")
        bank.store(game_id="g2", player_role="seer", situation="s2", decision="d2",
                   outcome="o2", lesson="l2")
        bank.store(game_id="g3", player_role="werewolf", situation="s3", decision="d3",
                   outcome="o3", lesson="l3")

        werewolf_mems = bank.retrieve("werewolf", k=5)
        assert len(werewolf_mems) == 2, f"Expected 2 werewolf memories, got {len(werewolf_mems)}"
        for mem in werewolf_mems:
            assert mem.player_role == "werewolf"

        seer_mems = bank.retrieve("seer", k=5)
        assert len(seer_mems) == 1, f"Expected 1 seer memory, got {len(seer_mems)}"
        assert seer_mems[0].player_role == "seer"

    def test_empty_retrieval(self, bank):
        """Retrieving for a role with no memories should return empty list."""
        results = bank.retrieve("hunter", k=5)
        assert results == [], f"Expected empty list for new role, got {results}"


class TestRetrieveKeywordMatch:
    """Tests for keyword-based memory retrieval."""

    def test_retrieve_keyword_match_priority(self, bank):
        """Memories with matching keywords should be ranked higher."""
        bank.store(game_id="g1", player_role="werewolf", situation="s1", decision="d1",
                   outcome="o1", lesson="l1", quality_score=0.5,
                   situation_keywords="night,first,kill")
        bank.store(game_id="g2", player_role="werewolf", situation="s2", decision="d2",
                   outcome="o2", lesson="l2", quality_score=0.9,
                   situation_keywords="day,vote,defense")
        bank.store(game_id="g3", player_role="werewolf", situation="s3", decision="d3",
                   outcome="o3", lesson="l3", quality_score=0.7,
                   situation_keywords="night,late,strategy")

        # Query with "night" keyword — should prefer memories with night
        results = bank.retrieve("werewolf", situation_keywords="night", k=3)
        assert len(results) == 3
        # First result should match "night" keywords
        has_night_high = any("night" in (res.situation_keywords or "").lower() for res in results[:1])
        assert has_night_high, f"Top result should match 'night' keyword, got: {[(r.situation_keywords, r.quality_score) for r in results]}"

    def test_retrieve_keyword_no_match_fallback(self, bank):
        """Memories without keyword match should still appear after matched ones."""
        bank.store(game_id="g1", player_role="werewolf", situation="s1", decision="d1",
                   outcome="o1", lesson="l1", quality_score=0.5,
                   situation_keywords="day,vote,defense")
        bank.store(game_id="g2", player_role="werewolf", situation="s2", decision="d2",
                   outcome="o2", lesson="l2", quality_score=0.9,
                   situation_keywords="night,first,kill")

        # Query without keywords — should return all, sorted by quality
        results = bank.retrieve("werewolf", k=5)
        assert len(results) == 2


class TestRetrieveScoring:
    """Tests for quality_score based ranking."""

    def test_retrieve_scoring_quality_effect(self, bank):
        """Higher quality scores should rank higher when no keyword match."""
        bank.store(game_id="g1", player_role="werewolf", situation="s1", decision="d1",
                   outcome="o1", lesson="l1", quality_score=0.3)
        bank.store(game_id="g2", player_role="werewolf", situation="s2", decision="d2",
                   outcome="o2", lesson="l2", quality_score=0.9)
        bank.store(game_id="g3", player_role="werewolf", situation="s3", decision="d3",
                   outcome="o3", lesson="l3", quality_score=0.5)

        results = bank.retrieve("werewolf", k=5)
        # The highest quality should be first (after keyword bonus)
        # q=0.9 gets score = 0.9 * 1/(1+0) = 0.9
        # q=0.5 gets score = 0.5 * 1/(1+0) = 0.5
        # q=0.3 gets score = 0.3 * 1/(1+0) = 0.3
        assert results[0].quality_score >= results[1].quality_score, (
            f"Higher quality should rank higher, got {[(r.quality_score, r.situation) for r in results]}"
        )


class TestRetrievalCount:
    """Tests for retrieval_count increment behavior."""

    def test_retrieval_count_increment(self, bank, repo):
        """Retrieval should increment the retrieval_count of returned memories."""
        mem_id = bank.store(
            game_id="g1", player_role="werewolf", situation="test",
            decision="test", outcome="test", lesson="test",
        )
        # Retrieve twice
        bank.retrieve("werewolf", k=5)
        bank.retrieve("werewolf", k=5)

        # Check count via repo directly
        mems = repo.get_memories(player_role="werewolf")
        assert len(mems) == 1
        assert mems[0].retrieval_count == 2, (
            f"Expected retrieval_count=2, got {mems[0].retrieval_count}"
        )


class TestUpdateQuality:
    """Tests for update_quality()."""

    def test_update_quality(self, bank, repo):
        """Quality score should be updated correctly."""
        mem_id = bank.store(
            game_id="g1", player_role="werewolf", situation="test",
            decision="test", outcome="test", lesson="test",
            quality_score=0.5,
        )
        bank.update_quality(mem_id, new_score=0.9, effectiveness=0.8)
        mems = repo.get_memories(player_role="werewolf")
        assert len(mems) == 1
        assert mems[0].quality_score == 0.9, f"Expected quality 0.9, got {mems[0].quality_score}"
        assert mems[0].effectiveness_rating == 0.8, f"Expected effectiveness 0.8, got {mems[0].effectiveness_rating}"


class TestGetStats:
    """Tests for get_stats()."""

    def test_get_stats_empty(self, bank):
        """Empty memory bank should return zero stats."""
        stats = bank.get_stats()
        assert stats["total"] == 0
        assert stats["avg_quality"] == 0.0

    def test_get_stats_with_memories(self, bank):
        """Stats should reflect stored memories."""
        bank.store(game_id="g1", player_role="werewolf", situation="s1", decision="d1",
                   outcome="o1", lesson="l1", quality_score=0.8)
        bank.store(game_id="g2", player_role="seer", situation="s2", decision="d2",
                   outcome="o2", lesson="l2", quality_score=0.4)
        bank.store(game_id="g3", player_role="werewolf", situation="s3", decision="d3",
                   outcome="o3", lesson="l3", quality_score=0.6)

        stats = bank.get_stats()
        assert stats["total"] == 3
        assert 0.5 < stats["avg_quality"] < 0.7, f"Expected avg ~0.6, got {stats['avg_quality']}"
        assert stats["per_role"]["werewolf"] == 2
        assert stats["per_role"]["seer"] == 1


class TestMemoryScore:
    """Tests for the _score static method."""

    def test_score_formula(self):
        """Score should equal quality_score * 1/(1+retrieval_count)."""
        # Fresh memory (0 retrievals, quality 0.8)
        score = MemoryBank._score(retrieval_count=0, quality_score=0.8)
        assert score == 0.8

        # Often-retrieved memory (5 retrievals, quality 0.9)
        score = MemoryBank._score(retrieval_count=5, quality_score=0.9)
        assert score == 0.9 * (1.0 / 6.0)

        # Low quality, many retrievals
        score = MemoryBank._score(retrieval_count=10, quality_score=0.2)
        assert score == 0.2 * (1.0 / 11.0)
