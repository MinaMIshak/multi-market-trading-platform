from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from enum import StrEnum
import json

from app.domain import MarketSession
from app.domain.enums import MarketSessionStatus
from app.storage.database import Database


class MarketSessionTransitionResult(StrEnum):
    CREATED = "CREATED"
    REPLACED = "REPLACED"
    UNCHANGED = "UNCHANGED"
    CONFLICT = "CONFLICT"


class MarketSessionTransitionRepository:
    """
    Atomic compare-and-promote operations for
    market_sessions.

    Existing states are replaced only when the
    caller explicitly declares them replaceable.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _json(session: MarketSession) -> str:
        return json.dumps(
            session.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def compare_and_promote(
        self,
        session: MarketSession,
        *,
        replaceable_statuses: Iterable[
            MarketSessionStatus
        ] = (),
    ) -> MarketSessionTransitionResult:
        replaceable = set(replaceable_statuses)

        with self.database.connect() as connection:
            # Acquire the write reservation before
            # reading the current state.
            connection.execute("BEGIN IMMEDIATE")

            row = connection.execute(
                """
                SELECT status
                FROM market_sessions
                WHERE market_date = ?
                """,
                (session.market_date.isoformat(),),
            ).fetchone()

            if row is None:
                connection.execute(
                    """
                    INSERT INTO market_sessions (
                        market_date,
                        status,
                        payload_json,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        session.market_date.isoformat(),
                        session.status.value,
                        self._json(session),
                        datetime.now(
                            timezone.utc
                        ).isoformat(),
                    ),
                )

                return (
                    MarketSessionTransitionResult.CREATED
                )

            current = MarketSessionStatus(
                row["status"]
            )

            if current == session.status:
                return (
                    MarketSessionTransitionResult.UNCHANGED
                )

            if current not in replaceable:
                return (
                    MarketSessionTransitionResult.CONFLICT
                )

            connection.execute(
                """
                UPDATE market_sessions
                SET
                    status = ?,
                    payload_json = ?,
                    updated_at = ?
                WHERE market_date = ?
                  AND status = ?
                """,
                (
                    session.status.value,
                    self._json(session),
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
                    session.market_date.isoformat(),
                    current.value,
                ),
            )

            return (
                MarketSessionTransitionResult.REPLACED
            )
