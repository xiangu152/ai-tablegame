from __future__ import annotations

from dataclasses import dataclass

from werewolf.storage.models import MemoryRecord
from werewolf.storage.repository import Repository


@dataclass
class Memory:
    id: int | None
    game_id: str
    player_role: str
    situation: str
    decision: str
    outcome: str
    lesson: str
    quality_score: float = 0.5
    retrieval_count: int = 0
    effectiveness_rating: float | None = None
    situation_keywords: str = ""
    embedding: str | None = None


class MemoryBank:
    """Manages the cross-game learning memory system.

    Storage: SQLite via Repository
    Retrieval V1: keyword-match + quality_score based relevance ranking
    Scoring: quality_score * 1/(1+retrieval_count) -- prefers high-quality, less-frequently-used memories
    """

    def __init__(self, repo: Repository):
        self.repo = repo

    def store(
        self,
        game_id: str,
        player_role: str,
        situation: str,
        decision: str,
        outcome: str,
        lesson: str,
        quality_score: float = 0.5,
        situation_keywords: str = "",
    ) -> int:
        """Store a memory. Returns memory_id."""
        record = MemoryRecord(
            game_id=game_id,
            player_role=player_role,
            situation=situation,
            decision=decision,
            outcome=outcome,
            lesson=lesson,
            quality_score=quality_score,
            retrieval_count=0,
            effectiveness_rating=None,
            situation_keywords=situation_keywords,
            embedding=None,
        )
        return self.repo.save_memory(record)

    def retrieve(
        self,
        player_role: str,
        situation_keywords: str = "",
        k: int = 3,
    ) -> list[Memory]:
        """
        Retrieve top-K memories for a role, scored by:
        quality_score * 1/(1+retrieval_count)

        Filtering (V1):
        1. Match by player_role
        2. If situation_keywords provided, prefer memories whose situation_keywords overlap
           (simple: count matching comma-separated keywords)
        3. Sort by weighted score descending
        4. Return top K

        Side effect: increments retrieval_count for returned memories.
        """
        records = self.repo.get_memories(player_role=player_role)

        if not records:
            return []

        query_keywords: set[str] = set()
        if situation_keywords:
            query_keywords = {
                kw.strip().lower()
                for kw in situation_keywords.split(",")
                if kw.strip()
            }

        scored: list[tuple[Memory, float, int]] = []
        for rec in records:
            mem = _record_to_memory(rec)
            base_score = self._score(mem.retrieval_count, mem.quality_score)

            keyword_bonus = 0
            if query_keywords and mem.situation_keywords:
                mem_keywords = {
                    kw.strip().lower()
                    for kw in mem.situation_keywords.split(",")
                    if kw.strip()
                }
                keyword_bonus = len(query_keywords & mem_keywords)

            # Memories with keyword overlap get priority; those without still considered
            scored.append((mem, base_score, keyword_bonus))

        # Sort: keyword matches first, then by score descending
        scored.sort(key=lambda x: (x[2], x[1]), reverse=True)

        # Take top K (if fewer than K, return all)
        top = scored[:k]

        # Increment retrieval_count for returned memories
        for mem, _, _ in top:
            if mem.id is not None:
                self.repo.increment_retrieval_count(mem.id)

        return [mem for mem, _, _ in top]

    def update_quality(
        self,
        memory_id: int,
        new_score: float,
        effectiveness: float | None = None,
    ):
        """Update quality_score and optionally effectiveness_rating."""
        self.repo.update_memory_quality(memory_id, new_score)
        if effectiveness is not None:
            self.repo.update_memory_effectiveness(memory_id, effectiveness)

    def get_stats(self) -> dict:
        """Return stats: total memories, per-role counts, avg quality."""
        return self.repo.get_memory_stats()

    @staticmethod
    def _score(retrieval_count: int, quality_score: float) -> float:
        """Calculate relevance score."""
        return quality_score * (1.0 / (1.0 + retrieval_count))


def _record_to_memory(rec: MemoryRecord) -> Memory:
    """Convert a MemoryRecord (DB-facing) to a Memory (user-facing)."""
    return Memory(
        id=rec.id,
        game_id=rec.game_id or "",
        player_role=rec.player_role or "",
        situation=rec.situation or "",
        decision=rec.decision or "",
        outcome=rec.outcome or "",
        lesson=rec.lesson or "",
        quality_score=rec.quality_score,
        retrieval_count=rec.retrieval_count,
        effectiveness_rating=rec.effectiveness_rating,
        situation_keywords=rec.situation_keywords or "",
        embedding=rec.embedding,
    )
