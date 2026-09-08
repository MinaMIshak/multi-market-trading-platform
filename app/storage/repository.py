from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from app.domain import (
    Candidate,
    MarketSession,
    Position,
    RiskDecision,
    Signal,
    TradeOutcome,
    TradePlan,
)
from app.storage.database import Database


def _utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _json(model: BaseModel) -> str:
    payload = model.model_dump(
        mode="json",
    )

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class TradingRepository:
    def __init__(
        self,
        database: Database,
    ) -> None:
        self.database = database

    def save_market_session(
        self,
        session: MarketSession,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO market_sessions (
                    market_date,
                    status,
                    payload_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(market_date)
                DO UPDATE SET
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    session.market_date.isoformat(),
                    session.status.value,
                    _json(session),
                    _utc_now(),
                ),
            )

    def save_candidate(
        self,
        candidate: Candidate,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO candidates (
                    candidate_id,
                    market_date,
                    symbol,
                    source,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id)
                DO UPDATE SET
                    payload_json = excluded.payload_json
                """,
                (
                    str(candidate.candidate_id),
                    candidate.market_date.isoformat(),
                    candidate.symbol,
                    candidate.source.value,
                    _json(candidate),
                    _utc_now(),
                ),
            )

    def save_signal(
        self,
        signal: Signal,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO signals (
                    signal_id,
                    candidate_id,
                    symbol,
                    strategy,
                    status,
                    created_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id)
                DO UPDATE SET
                    status = excluded.status,
                    payload_json = excluded.payload_json
                """,
                (
                    str(signal.signal_id),
                    (
                        str(signal.candidate_id)
                        if signal.candidate_id
                        else None
                    ),
                    signal.symbol,
                    signal.strategy.value,
                    signal.status.value,
                    signal.created_at.isoformat(),
                    _json(signal),
                ),
            )

    def save_trade_plan(
        self,
        plan: TradePlan,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO trade_plans (
                    trade_plan_id,
                    signal_id,
                    symbol,
                    created_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trade_plan_id)
                DO UPDATE SET
                    payload_json = excluded.payload_json
                """,
                (
                    str(plan.trade_plan_id),
                    str(plan.signal_id),
                    plan.symbol,
                    plan.created_at.isoformat(),
                    _json(plan),
                ),
            )

    def save_risk_decision(
        self,
        decision: RiskDecision,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO risk_decisions (
                    risk_decision_id,
                    trade_plan_id,
                    decision,
                    created_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(risk_decision_id)
                DO UPDATE SET
                    decision = excluded.decision,
                    payload_json = excluded.payload_json
                """,
                (
                    str(
                        decision.risk_decision_id
                    ),
                    str(decision.trade_plan_id),
                    decision.decision.value,
                    _utc_now(),
                    _json(decision),
                ),
            )

    def save_position(
        self,
        position: Position,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO positions (
                    position_id,
                    trade_plan_id,
                    symbol,
                    state,
                    opened_at,
                    closed_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id)
                DO UPDATE SET
                    state = excluded.state,
                    closed_at = excluded.closed_at,
                    payload_json = excluded.payload_json
                """,
                (
                    str(position.position_id),
                    str(position.trade_plan_id),
                    position.symbol,
                    position.state.value,
                    position.opened_at.isoformat(),
                    (
                        position.closed_at.isoformat()
                        if position.closed_at
                        else None
                    ),
                    _json(position),
                ),
            )

    def save_trade_outcome(
        self,
        outcome: TradeOutcome,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO trade_outcomes (
                    outcome_id,
                    position_id,
                    trade_plan_id,
                    symbol,
                    status,
                    created_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(outcome_id)
                DO UPDATE SET
                    status = excluded.status,
                    payload_json = excluded.payload_json
                """,
                (
                    str(outcome.outcome_id),
                    (
                        str(outcome.position_id)
                        if outcome.position_id
                        else None
                    ),
                    str(outcome.trade_plan_id),
                    outcome.symbol,
                    outcome.status.value,
                    _utc_now(),
                    _json(outcome),
                ),
            )

    def record_audit_event(
        self,
        *,
        event_type: str,
        entity_type: str,
        entity_id: str | None = None,
        market_date: str | None = None,
        payload: dict[str, Any] | None = None,
        event_key: str | None = None,
    ) -> bool:
        event_id = str(uuid4())

        payload_json = json.dumps(
            payload or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO audit_events (
                        event_id,
                        event_key,
                        event_type,
                        entity_type,
                        entity_id,
                        market_date,
                        created_at,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        event_key,
                        event_type,
                        entity_type,
                        entity_id,
                        market_date,
                        _utc_now(),
                        payload_json,
                    ),
                )

                return cursor.rowcount == 1

        except Exception:
            raise

    def count_rows(
        self,
        table: str,
    ) -> int:
        allowed_tables = {
            "market_sessions",
            "candidates",
            "signals",
            "trade_plans",
            "risk_decisions",
            "positions",
            "trade_outcomes",
            "audit_events",
        }

        if table not in allowed_tables:
            raise ValueError(
                f"unsupported table: {table}"
            )

        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()

        return int(row["count"])
