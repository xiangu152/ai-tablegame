from __future__ import annotations

import json
import uuid
from typing import Optional

from .db import get_connection, init_db
from .models import (
    DialogueRecord,
    EventRecord,
    GamePlayer,
    GameRecord,
    GameResult,
    MemoryRecord,
    PlayerRecord,
    RoundRecord,
    VoteRecord,
)


class Repository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn = init_db(db_path)

    @property
    def conn(self):
        return self._conn

    def _new_id(self) -> str:
        return str(uuid.uuid4())

    def create_game(self, game: GameRecord) -> str:
        self._conn.execute(
            "INSERT INTO games (id, mode, winner, total_rounds, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?)",
            (game.id, game.mode, game.winner, game.total_rounds, game.started_at, game.ended_at),
        )
        self._conn.commit()
        return game.id

    def create_player(self, player: PlayerRecord) -> str:
        self._conn.execute(
            "INSERT INTO players (id, name) VALUES (?, ?)",
            (player.id, player.name),
        )
        self._conn.commit()
        return player.id

    def add_game_player(self, game_id: str, player_id: str, role: str, seat_number: int) -> None:
        self._conn.execute(
            "INSERT INTO game_players (game_id, player_id, role, seat_number, is_alive) VALUES (?, ?, ?, ?, ?)",
            (game_id, player_id, role, seat_number, True),
        )
        self._conn.commit()

    def create_round(self, round_: RoundRecord) -> str:
        self._conn.execute(
            "INSERT INTO rounds (id, game_id, round_num, phase) VALUES (?, ?, ?, ?)",
            (round_.id, round_.game_id, round_.round_num, round_.phase),
        )
        self._conn.commit()
        return round_.id

    def log_event(self, event: EventRecord) -> str:
        self._conn.execute(
            "INSERT INTO events (id, round_id, event_type, player_id, payload, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (event.id, event.round_id, event.event_type, event.player_id, event.payload, event.timestamp),
        )
        self._conn.commit()
        return event.id

    def log_dialogue(self, dialogue: DialogueRecord) -> str:
        self._conn.execute(
            "INSERT INTO dialogues (id, round_id, player_id, content, reasoning) VALUES (?, ?, ?, ?, ?)",
            (dialogue.id, dialogue.round_id, dialogue.player_id, dialogue.content, dialogue.reasoning),
        )
        self._conn.commit()
        return dialogue.id

    def log_vote(self, vote: VoteRecord) -> str:
        self._conn.execute(
            "INSERT INTO votes (id, round_id, voter_id, target_id, vote_type) VALUES (?, ?, ?, ?, ?)",
            (vote.id, vote.round_id, vote.voter_id, vote.target_id, vote.vote_type),
        )
        self._conn.commit()
        return vote.id

    def save_game_result(self, result: GameResult) -> None:
        self._conn.execute(
            "INSERT INTO game_results (game_id, winner, duration_rounds, mvp_player_id, analysis_report) VALUES (?, ?, ?, ?, ?)",
            (result.game_id, result.winner, result.duration_rounds, result.mvp_player_id, result.analysis_report),
        )
        self._conn.commit()

    def get_game(self, game_id: str) -> Optional[GameRecord]:
        row = self._conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if row is None:
            return None
        return GameRecord(**dict(row))

    def get_rounds(self, game_id: str) -> list[RoundRecord]:
        rows = self._conn.execute(
            "SELECT * FROM rounds WHERE game_id = ? ORDER BY round_num", (game_id,)
        ).fetchall()
        return [RoundRecord(**dict(r)) for r in rows]

    def get_events(self, round_id: str) -> list[EventRecord]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE round_id = ? ORDER BY timestamp", (round_id,)
        ).fetchall()
        return [EventRecord(**dict(r)) for r in rows]

    def get_dialogues(self, round_id: str) -> list[DialogueRecord]:
        rows = self._conn.execute(
            "SELECT * FROM dialogues WHERE round_id = ?", (round_id,)
        ).fetchall()
        return [DialogueRecord(**dict(r)) for r in rows]

    def get_votes(self, round_id: str) -> list[VoteRecord]:
        rows = self._conn.execute(
            "SELECT * FROM votes WHERE round_id = ?", (round_id,)
        ).fetchall()
        return [VoteRecord(**dict(r)) for r in rows]

    def get_game_players(self, game_id: str) -> list[tuple]:
        rows = self._conn.execute(
            "SELECT player_id, role, seat_number, is_alive FROM game_players WHERE game_id = ? ORDER BY seat_number",
            (game_id,),
        ).fetchall()
        return [tuple(r) for r in rows]

    def get_all_games(self) -> list[GameRecord]:
        rows = self._conn.execute(
            "SELECT * FROM games ORDER BY started_at DESC"
        ).fetchall()
        return [GameRecord(**dict(r)) for r in rows]

    def get_game_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as count FROM games").fetchone()
        return row["count"]

    def get_per_role_win_rates(self) -> list[dict]:
        rows = self._conn.execute("""
            SELECT
                gp.role,
                COUNT(DISTINCT g.id) as total_games,
                COUNT(DISTINCT CASE WHEN g.winner = 'werewolves' THEN g.id END) as werewolf_wins,
                COUNT(DISTINCT CASE WHEN g.winner = 'villagers' THEN g.id END) as villager_wins
            FROM game_players gp
            JOIN games g ON gp.game_id = g.id
            GROUP BY gp.role
        """).fetchall()
        return [dict(r) for r in rows]

    def save_memory(self, memory: MemoryRecord) -> int:
        cursor = self._conn.execute(
            """INSERT INTO memories
               (game_id, player_role, situation, decision, outcome, lesson,
                quality_score, retrieval_count, effectiveness_rating,
                situation_keywords, embedding)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory.game_id, memory.player_role, memory.situation, memory.decision,
                memory.outcome, memory.lesson, memory.quality_score, memory.retrieval_count,
                memory.effectiveness_rating, memory.situation_keywords, memory.embedding,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_memories(self, player_role: Optional[str] = None, limit: int = 100) -> list[MemoryRecord]:
        if player_role:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE player_role = ? ORDER BY created_at DESC LIMIT ?",
                (player_role, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [MemoryRecord(**dict(r)) for r in rows]

    def update_memory_effectiveness(self, memory_id: int, rating: float) -> None:
        self._conn.execute(
            "UPDATE memories SET effectiveness_rating = ? WHERE id = ?",
            (rating, memory_id),
        )
        self._conn.commit()

    def increment_retrieval_count(self, memory_id: int) -> None:
        self._conn.execute(
            "UPDATE memories SET retrieval_count = retrieval_count + 1 WHERE id = ?",
            (memory_id,),
        )
        self._conn.commit()

    def update_memory_quality(self, memory_id: int, quality_score: float) -> None:
        self._conn.execute(
            "UPDATE memories SET quality_score = ? WHERE id = ?",
            (quality_score, memory_id),
        )
        self._conn.commit()

    def get_memory_stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*) as total, AVG(quality_score) as avg_quality FROM memories"
        ).fetchone()
        per_role = self._conn.execute(
            "SELECT player_role, COUNT(*) as count FROM memories GROUP BY player_role"
        ).fetchall()
        return {
            "total": row["total"],
            "avg_quality": round(row["avg_quality"], 3) if row["avg_quality"] is not None else 0.0,
            "per_role": {r["player_role"]: r["count"] for r in per_role},
        }

    def close(self) -> None:
        self._conn.close()
