from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_VERSION = 9


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_artifacts (
    ingestion_id TEXT PRIMARY KEY REFERENCES data_ingestions(ingestion_id) ON DELETE RESTRICT,
    contract TEXT NOT NULL CHECK (contract IN (
        'egx-universe-v1', 'egx-actions-v1', 'egx-source-review-v1'
    )),
    effective_date TEXT,
    instrument_id TEXT,
    coverage_start TEXT,
    coverage_end TEXT,
    subject_ingestion_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_reference_date
ON reference_artifacts(contract, effective_date);
CREATE INDEX IF NOT EXISTS idx_reference_instrument
ON reference_artifacts(contract, instrument_id, coverage_start, coverage_end);
CREATE INDEX IF NOT EXISTS idx_reference_subject
ON reference_artifacts(contract, subject_ingestion_id);

CREATE TRIGGER IF NOT EXISTS reference_artifacts_no_update
BEFORE UPDATE ON reference_artifacts BEGIN
    SELECT RAISE(ABORT, 'immutable reference artifact');
END;
CREATE TRIGGER IF NOT EXISTS reference_artifacts_no_delete
BEFORE DELETE ON reference_artifacts BEGIN
    SELECT RAISE(ABORT, 'immutable reference artifact');
END;

CREATE TABLE IF NOT EXISTS automatic_quota (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    quota_day TEXT NOT NULL,
    used_units INTEGER NOT NULL CHECK (typeof(used_units) = 'integer' AND used_units >= 0)
);

INSERT OR IGNORE INTO automatic_quota (id, quota_day, used_units)
VALUES (1, '0001-01-01', 0);

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


CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,

    market_date TEXT NOT NULL,
    checkpoint_name TEXT NOT NULL,

    status TEXT NOT NULL,

    scheduled_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,

    calendar_truth TEXT NOT NULL,

    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,

    attempt_count INTEGER NOT NULL DEFAULT 0,

    started_at TEXT,
    finished_at TEXT,
    last_error TEXT,

    UNIQUE (
        market_date,
        checkpoint_name
    )
);

CREATE INDEX IF NOT EXISTS
idx_scheduled_jobs_date_status
ON scheduled_jobs (
    market_date,
    status
);

CREATE INDEX IF NOT EXISTS
idx_scheduled_jobs_checkpoint
ON scheduled_jobs (
    checkpoint_name
);



CREATE TABLE IF NOT EXISTS data_ingestions (
    ingestion_id TEXT PRIMARY KEY,

    provider TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    granularity TEXT,

    symbol TEXT,
    market_date TEXT,

    raw_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    record_count INTEGER,

    status TEXT NOT NULL,
    source_uri TEXT,

    received_at TEXT NOT NULL,
    completed_at TEXT,

    metadata_json TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS
idx_data_ingestions_raw_path
ON data_ingestions (
    raw_path
);

CREATE INDEX IF NOT EXISTS
idx_data_ingestions_provider_date
ON data_ingestions (
    provider,
    market_date
);

CREATE INDEX IF NOT EXISTS
idx_data_ingestions_symbol_date
ON data_ingestions (
    symbol,
    market_date
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id TEXT PRIMARY KEY,

    ingestion_id TEXT,

    severity TEXT NOT NULL,
    code TEXT NOT NULL,

    symbol TEXT,
    market_date TEXT,

    message TEXT NOT NULL,
    payload_json TEXT NOT NULL,

    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS
idx_data_quality_ingestion
ON data_quality_issues (
    ingestion_id
);

CREATE INDEX IF NOT EXISTS
idx_data_quality_severity
ON data_quality_issues (
    severity
);

CREATE INDEX IF NOT EXISTS
idx_data_quality_code
ON data_quality_issues (
    code
);



CREATE TABLE IF NOT EXISTS canonical_instruments (
    instrument_id TEXT PRIMARY KEY,

    instrument_type TEXT NOT NULL,

    canonical_ticker TEXT NOT NULL,

    name_en TEXT,
    name_ar TEXT,

    short_name_en TEXT,
    short_name_ar TEXT,

    source_provider TEXT NOT NULL,
    source_symbol_code TEXT NOT NULL,

    reuters_raw TEXT,
    reuters_normalized TEXT,

    source_sha256 TEXT NOT NULL,
    source_market_date TEXT,

    normalization_notes_json TEXT NOT NULL,

    updated_at TEXT NOT NULL,

    UNIQUE (
        source_provider,
        source_symbol_code
    )
);

CREATE INDEX IF NOT EXISTS
idx_canonical_instruments_ticker
ON canonical_instruments (
    canonical_ticker
);

CREATE INDEX IF NOT EXISTS
idx_canonical_instruments_type
ON canonical_instruments (
    instrument_type
);

CREATE TABLE IF NOT EXISTS instrument_aliases (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,

    instrument_id TEXT NOT NULL,

    provider TEXT NOT NULL,
    alias_type TEXT NOT NULL,

    alias_value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,

    created_at TEXT NOT NULL,

    FOREIGN KEY (
        instrument_id
    )
    REFERENCES canonical_instruments (
        instrument_id
    )
    ON DELETE CASCADE,

    UNIQUE (
        instrument_id,
        provider,
        alias_type,
        normalized_value
    )
);

CREATE INDEX IF NOT EXISTS
idx_instrument_alias_lookup
ON instrument_aliases (
    normalized_value
);

CREATE INDEX IF NOT EXISTS
idx_instrument_alias_provider
ON instrument_aliases (
    provider,
    normalized_value
);


CREATE TABLE IF NOT EXISTS canonical_data_artifacts (
    artifact_id TEXT PRIMARY KEY,

    provider TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    granularity TEXT NOT NULL,

    symbol TEXT NOT NULL,
    source_snapshot_date TEXT NOT NULL,

    canonical_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,

    byte_size INTEGER NOT NULL,
    record_count INTEGER NOT NULL,

    oldest_market_date TEXT NOT NULL,
    newest_market_date TEXT NOT NULL,

    semantic_contract_version TEXT NOT NULL,
    serialization_format TEXT NOT NULL,

    full_ohlc_valid_count INTEGER NOT NULL,
    legacy_close_reference_count INTEGER NOT NULL,
    quarantined_anomaly_count INTEGER NOT NULL,

    full_ohlc_usable_count INTEGER NOT NULL,
    close_history_usable_count INTEGER NOT NULL,

    status TEXT NOT NULL,

    created_at TEXT NOT NULL,
    validated_at TEXT,

    metadata_json TEXT NOT NULL,

    CHECK (byte_size >= 0),
    CHECK (record_count >= 0),

    CHECK (full_ohlc_valid_count >= 0),
    CHECK (legacy_close_reference_count >= 0),
    CHECK (quarantined_anomaly_count >= 0),

    CHECK (full_ohlc_usable_count >= 0),
    CHECK (close_history_usable_count >= 0),

    CHECK (
        full_ohlc_valid_count
        + legacy_close_reference_count
        + quarantined_anomaly_count
        = record_count
    ),

    CHECK (
        full_ohlc_usable_count
        <= record_count
    ),

    CHECK (
        close_history_usable_count
        <= record_count
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS
idx_canonical_artifacts_path
ON canonical_data_artifacts (
    canonical_path
);

CREATE UNIQUE INDEX IF NOT EXISTS
idx_canonical_artifacts_snapshot_contract
ON canonical_data_artifacts (
    provider,
    asset_type,
    granularity,
    symbol,
    source_snapshot_date,
    semantic_contract_version,
    serialization_format
);

CREATE INDEX IF NOT EXISTS
idx_canonical_artifacts_symbol_date
ON canonical_data_artifacts (
    symbol,
    source_snapshot_date
);


CREATE TABLE IF NOT EXISTS canonical_artifact_sources (
    artifact_id TEXT NOT NULL,
    ingestion_id TEXT NOT NULL,

    source_ordinal INTEGER NOT NULL,

    PRIMARY KEY (
        artifact_id,
        ingestion_id
    ),

    UNIQUE (
        artifact_id,
        source_ordinal
    ),

    CHECK (
        source_ordinal >= 1
    ),

    FOREIGN KEY (
        artifact_id
    )
    REFERENCES canonical_data_artifacts (
        artifact_id
    )
    ON DELETE CASCADE,

    FOREIGN KEY (
        ingestion_id
    )
    REFERENCES data_ingestions (
        ingestion_id
    )
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS
idx_canonical_artifact_sources_ingestion
ON canonical_artifact_sources (
    ingestion_id
);


CREATE TABLE IF NOT EXISTS daily_canonical_artifacts (
    artifact_id TEXT PRIMARY KEY,

    instrument_id TEXT NOT NULL,
    canonical_symbol TEXT NOT NULL,

    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,

    source_snapshot_date TEXT NOT NULL,

    canonical_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,

    byte_size INTEGER NOT NULL,
    record_count INTEGER NOT NULL,

    oldest_market_date TEXT NOT NULL,
    newest_market_date TEXT NOT NULL,

    valid_bar_count INTEGER NOT NULL,
    quarantined_bar_count INTEGER NOT NULL,

    semantic_contract_version TEXT NOT NULL,
    serialization_format TEXT NOT NULL,

    status TEXT NOT NULL,

    created_at TEXT NOT NULL,
    validated_at TEXT,

    metadata_json TEXT NOT NULL,

    FOREIGN KEY (instrument_id)
    REFERENCES canonical_instruments (instrument_id)
    ON DELETE RESTRICT,

    CHECK (byte_size >= 0),
    CHECK (record_count >= 0),
    CHECK (valid_bar_count >= 0),
    CHECK (quarantined_bar_count >= 0),

    CHECK (
        valid_bar_count
        + quarantined_bar_count
        = record_count
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS
idx_daily_canonical_snapshot_contract
ON daily_canonical_artifacts (
    provider,
    instrument_id,
    provider_symbol,
    source_snapshot_date,
    semantic_contract_version,
    serialization_format
);

CREATE INDEX IF NOT EXISTS
idx_daily_canonical_symbol_date
ON daily_canonical_artifacts (
    canonical_symbol,
    source_snapshot_date
);


CREATE TABLE IF NOT EXISTS daily_canonical_sources (
    artifact_id TEXT NOT NULL,
    ingestion_id TEXT NOT NULL,
    source_ordinal INTEGER NOT NULL,

    PRIMARY KEY (
        artifact_id,
        ingestion_id
    ),

    UNIQUE (
        artifact_id,
        source_ordinal
    ),

    CHECK (source_ordinal >= 1),

    FOREIGN KEY (artifact_id)
    REFERENCES daily_canonical_artifacts (artifact_id)
    ON DELETE CASCADE,

    FOREIGN KEY (ingestion_id)
    REFERENCES data_ingestions (ingestion_id)
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS
idx_daily_canonical_sources_ingestion
ON daily_canonical_sources (
    ingestion_id
);


CREATE TABLE IF NOT EXISTS holiday_evidence (
    evidence_id TEXT PRIMARY KEY,

    authority TEXT NOT NULL,
    observed_date TEXT NOT NULL,
    nominal_date TEXT,

    market_closed INTEGER NOT NULL,

    source_uri TEXT NOT NULL,
    source_published_at TEXT,

    content_hash TEXT NOT NULL,
    received_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL,

    CHECK (
        market_closed IN (0, 1)
    ),

    UNIQUE (
        authority,
        observed_date,
        source_uri,
        content_hash
    )
);

CREATE INDEX IF NOT EXISTS
idx_holiday_evidence_observed_date
ON holiday_evidence (
    observed_date
);

CREATE INDEX IF NOT EXISTS
idx_holiday_evidence_authority_date
ON holiday_evidence (
    authority,
    observed_date
);

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

    def initialize(
        self,
        *,
        allow_upgrade: bool = False,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "PRAGMA journal_mode = WAL"
            )

            connection.execute(
                "PRAGMA synchronous = NORMAL"
            )

            tables = {
                row["name"]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name NOT LIKE 'sqlite_%'
                    """
                ).fetchall()
            }

            if "schema_meta" not in tables:
                if tables:
                    raise RuntimeError(
                        "database schema metadata missing"
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
                    """,
                    (str(SCHEMA_VERSION),),
                )

                return

            row = connection.execute(
                """
                SELECT value
                FROM schema_meta
                WHERE key = 'schema_version'
                """
            ).fetchone()

            if row is None:
                raise RuntimeError(
                    "database schema version missing"
                )

            try:
                existing_version = int(
                    row["value"]
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "invalid database schema version"
                ) from exc

            if existing_version > SCHEMA_VERSION:
                raise RuntimeError(
                    "database schema is newer than "
                    "application schema: "
                    f"{existing_version} > "
                    f"{SCHEMA_VERSION}"
                )

            if (
                existing_version < SCHEMA_VERSION
                and not allow_upgrade
            ):
                raise RuntimeError(
                    "database schema upgrade required: "
                    f"{existing_version} -> "
                    f"{SCHEMA_VERSION}"
                )

            connection.executescript(
                SCHEMA_SQL
            )

            if existing_version < SCHEMA_VERSION:
                connection.execute(
                    """
                    UPDATE schema_meta
                    SET value = ?
                    WHERE key = 'schema_version'
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
