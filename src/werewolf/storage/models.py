from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class GameRecord:
    id: str
    mode: str
    winner: str
    total_rounds: int
    started_at: datetime
    ended_at: Optional[datetime] = None


@dataclass
class PlayerRecord:
    id: str
    name: str


@dataclass
class GamePlayer:
    game_id: str
    player_id: str
    role: str
    seat_number: int
    is_alive: bool = True


@dataclass
class RoundRecord:
    id: str
    game_id: str
    round_num: int
    phase: str


@dataclass
class EventRecord:
    id: str
    round_id: str
    event_type: str
    player_id: Optional[str] = None
    payload: Optional[str] = None
    timestamp: Optional[datetime] = None


@dataclass
class DialogueRecord:
    id: str
    round_id: str
    player_id: str
    content: str
    reasoning: str


@dataclass
class VoteRecord:
    id: str
    round_id: str
    voter_id: str
    target_id: str
    vote_type: str


@dataclass
class GameResult:
    game_id: str
    winner: str
    duration_rounds: int
    mvp_player_id: Optional[str] = None
    analysis_report: Optional[str] = None


@dataclass
class MemoryRecord:
    id: Optional[int] = None
    game_id: Optional[str] = None
    player_role: Optional[str] = None
    situation: Optional[str] = None
    decision: Optional[str] = None
    outcome: Optional[str] = None
    lesson: Optional[str] = None
    quality_score: float = 0.5
    retrieval_count: int = 0
    effectiveness_rating: Optional[float] = None
    situation_keywords: Optional[str] = None
    embedding: Optional[str] = None
    created_at: Optional[datetime] = None
