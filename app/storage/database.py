from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_VERSION = 1


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_sessions (
    market_date TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    market_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS
idx_candidates_market_symbol
ON candidates (market_date, symbol);

CREATE INDEX IF NOT EXISTS
idx_candidates_source
ON candidates (source);

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    candidate_id TEXT,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS
idx_signals_symbol_created
ON signals (symbol, created_at);

CREATE INDEX IF NOT EXISTS
idx_signals_status
ON signals (status);

CREATE TABLE IF NOT EXISTS trade_plans (
    trade_plan_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS
idx_trade_plans_signal
ON trade_plans (signal_id);

CREATE TABLE IF NOT EXISTS risk_decisions (
    risk_decision_id TEXT PRIMARY KEY,
    trade_plan_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS
idx_risk_trade_plan
ON risk_decisions (trade_plan_id);

CREATE INDEX IF NOT EXISTS
idx_risk_decision
ON risk_decisions (decision);

CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    trade_plan_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    state TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS
idx_positions_symbol
ON positions (symbol);

CREATE INDEX IF NOT EXISTS
idx_positions_state
ON positions (state);

CREATE TABLE IF NOT EXISTS trade_outcomes (
    outcome_id TEXT PRIMARY KEY,
    position_id TEXT,
    trade_plan_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS
idx_outcomes_symbol
ON trade_outcomes (symbol);

CREATE INDEX IF NOT EXISTS
idx_outcomes_status
ON trade_outcomes (status);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    event_key TEXT UNIQUE,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    market_date TEXT,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS
idx_audit_type_created
ON audit_events (event_type, created_at);

CREATE INDEX IF NOT EXISTS
idx_audit_entity
ON audit_events (entity_type, entity_id);

CREATE INDEX IF NOT EXISTS
idx_audit_market_date
ON audit_events (market_date);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        connection = sqlite3.connect(
            self.path,
            timeout=30,
        )

        connection.row_factory = sqlite3.Row

        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        connection.execute(
            "PRAGMA busy_timeout = 5000"
        )

        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "PRAGMA journal_mode = WAL"
            )

            connection.execute(
                "PRAGMA synchronous = NORMAL"
            )

            connection.executescript(
                SCHEMA_SQL
            )

            connection.execute(
                """
                INSERT INTO schema_meta (
                    key,
                    value
                )
                VALUES (
                    'schema_version',
                    ?
                )
                ON CONFLICT(key)
                DO UPDATE SET
                    value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

    def schema_version(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT value
                FROM schema_meta
                WHERE key = 'schema_version'
                """
            ).fetchone()

        if row is None:
            raise RuntimeError(
                "database schema is not initialized"
            )

        return int(row["value"])

    def health_check(self) -> bool:
        try:
            with self.connect() as connection:
                result = connection.execute(
                    "SELECT 1 AS ok"
                ).fetchone()

            return (
                result is not None
                and result["ok"] == 1
            )

        except sqlite3.Error:
            return False
