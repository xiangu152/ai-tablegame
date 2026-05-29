from __future__ import annotations

import sqlite3


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY,
            mode TEXT,
            winner TEXT,
            total_rounds INT,
            started_at TIMESTAMP,
            ended_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY,
            name TEXT
        );

        CREATE TABLE IF NOT EXISTS game_players (
            game_id TEXT REFERENCES games(id),
            player_id TEXT REFERENCES players(id),
            role TEXT,
            seat_number INT,
            is_alive BOOLEAN DEFAULT TRUE
        );

        CREATE TABLE IF NOT EXISTS rounds (
            id TEXT PRIMARY KEY,
            game_id TEXT REFERENCES games(id),
            round_num INT,
            phase TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            round_id TEXT REFERENCES rounds(id),
            event_type TEXT,
            player_id TEXT,
            payload JSON,
            timestamp TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS dialogues (
            id TEXT PRIMARY KEY,
            round_id TEXT REFERENCES rounds(id),
            player_id TEXT,
            content TEXT,
            reasoning TEXT
        );

        CREATE TABLE IF NOT EXISTS votes (
            id TEXT PRIMARY KEY,
            round_id TEXT REFERENCES rounds(id),
            voter_id TEXT,
            target_id TEXT,
            vote_type TEXT
        );

        CREATE TABLE IF NOT EXISTS game_results (
            game_id TEXT REFERENCES games(id),
            winner TEXT,
            duration_rounds INT,
            mvp_player_id TEXT,
            analysis_report TEXT
        );

        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT REFERENCES games(id),
            player_role TEXT,
            situation TEXT,
            decision TEXT,
            outcome TEXT,
            lesson TEXT,
            quality_score REAL DEFAULT 0.5,
            retrieval_count INTEGER DEFAULT 0,
            effectiveness_rating REAL,
            situation_keywords TEXT,
            embedding TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)


def init_db(db_path: str) -> sqlite3.Connection:
    conn = get_connection(db_path)
    create_tables(conn)
    conn.commit()
    return conn
