from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import re
from typing import Any
from uuid import uuid4

from app.storage.database import Database


_SHA256_RE = re.compile(
    r"^[0-9a-fA-F]{64}$"
)


class HolidayEvidenceConflictError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class HolidayEvidenceRecord:
    evidence_id: str
    authority: str
    observed_date: date
    nominal_date: date | None
    market_closed: bool
    source_uri: str
    source_published_at: datetime | None
    content_hash: str
    received_at: datetime
    metadata: dict[str, Any]


class HolidayEvidenceRepository:
    """
    Immutable holiday-evidence persistence.

    Identity:
      authority + observed_date
      + source_uri + content_hash

    Replaying identical evidence is idempotent.
    Conflicting interpretation of the same
    source content fails closed.
    """

    def __init__(
        self,
        database: Database,
    ) -> None:
        self.database = database

    @staticmethod
    def _require_aware(
        value: datetime,
        *,
        field_name: str,
    ) -> None:
        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                f"{field_name} must be timezone-aware"
            )

    @staticmethod
    def _canonical_metadata(
        metadata: dict[str, Any] | None,
    ) -> str:
        return json.dumps(
            metadata or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _row_to_record(
        row,
    ) -> HolidayEvidenceRecord:
        published = row[
            "source_published_at"
        ]

        return HolidayEvidenceRecord(
            evidence_id=row["evidence_id"],
            authority=row["authority"],
            observed_date=date.fromisoformat(
                row["observed_date"]
            ),
            nominal_date=(
                date.fromisoformat(
                    row["nominal_date"]
                )
                if row["nominal_date"]
                else None
            ),
            market_closed=bool(
                row["market_closed"]
            ),
            source_uri=row["source_uri"],
            source_published_at=(
                datetime.fromisoformat(
                    published
                )
                if published
                else None
            ),
            content_hash=row["content_hash"],
            received_at=datetime.fromisoformat(
                row["received_at"]
            ),
            metadata=json.loads(
                row["metadata_json"]
            ),
        )

    def save_evidence(
        self,
        *,
        authority: str,
        observed_date: date,
        market_closed: bool,
        source_uri: str,
        content_hash: str,
        received_at: datetime,
        nominal_date: date | None = None,
        source_published_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        authority = authority.strip()
        source_uri = source_uri.strip()
        content_hash = content_hash.lower()

        if not authority:
            raise ValueError(
                "authority must not be empty"
            )

        if not source_uri:
            raise ValueError(
                "source_uri must not be empty"
            )

        if not isinstance(
            market_closed,
            bool,
        ):
            raise ValueError(
                "market_closed must be bool"
            )

        if not _SHA256_RE.fullmatch(
            content_hash
        ):
            raise ValueError(
                "content_hash must be SHA-256 hex"
            )

        self._require_aware(
            received_at,
            field_name="received_at",
        )

        if source_published_at is not None:
            self._require_aware(
                source_published_at,
                field_name="source_published_at",
            )

        metadata_json = (
            self._canonical_metadata(
                metadata
            )
        )

        nominal_text = (
            nominal_date.isoformat()
            if nominal_date
            else None
        )

        published_text = (
            source_published_at.isoformat()
            if source_published_at
            else None
        )

        identity = (
            authority,
            observed_date.isoformat(),
            source_uri,
            content_hash,
        )

        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO
                holiday_evidence (
                    evidence_id,
                    authority,
                    observed_date,
                    nominal_date,
                    market_closed,
                    source_uri,
                    source_published_at,
                    content_hash,
                    received_at,
                    metadata_json
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    str(uuid4()),
                    authority,
                    observed_date.isoformat(),
                    nominal_text,
                    int(market_closed),
                    source_uri,
                    published_text,
                    content_hash,
                    received_at.isoformat(),
                    metadata_json,
                ),
            )

            if cursor.rowcount == 1:
                return True

            row = connection.execute(
                """
                SELECT
                    nominal_date,
                    market_closed,
                    source_published_at,
                    metadata_json
                FROM holiday_evidence
                WHERE authority = ?
                  AND observed_date = ?
                  AND source_uri = ?
                  AND content_hash = ?
                """,
                identity,
            ).fetchone()

            if row is None:
                raise RuntimeError(
                    "holiday evidence replay "
                    "could not resolve existing row"
                )

            same = (
                row["nominal_date"]
                == nominal_text
                and row["market_closed"]
                == int(market_closed)
                and row["source_published_at"]
                == published_text
                and row["metadata_json"]
                == metadata_json
            )

            if not same:
                raise HolidayEvidenceConflictError(
                    "conflicting interpretation "
                    "for identical holiday evidence"
                )

            return False

    def list_for_observed_date(
        self,
        market_date: date,
    ) -> list[HolidayEvidenceRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    evidence_id,
                    authority,
                    observed_date,
                    nominal_date,
                    market_closed,
                    source_uri,
                    source_published_at,
                    content_hash,
                    received_at,
                    metadata_json
                FROM holiday_evidence
                WHERE observed_date = ?
                ORDER BY
                    received_at ASC,
                    evidence_id ASC
                """,
                (
                    market_date.isoformat(),
                ),
            ).fetchall()

        return [
            self._row_to_record(row)
            for row in rows
        ]
