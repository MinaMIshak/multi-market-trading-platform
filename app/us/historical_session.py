"""US2 retrospective US market-session admission.

This module admits exact historical US session/calendar facts for research.

It deliberately does not infer weekends, holidays, DST, regular hours,
early closes, or session truth from OHLCV observations.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from app.research.historical_evidence import (
    HistoricalEvidencePackage,
    require_historical_evidence,
)
from app.us.contracts import (
    USSessionRecord,
    US_MARKET_TIMEZONE,
)


US_SESSION_EVIDENCE_FIELDS = frozenset(
    {
        "market_date",
        "calendar_mic",
        "timezone_name",
        "state",
        "opens_at_utc",
        "closes_at_utc",
    }
)

_MIC_PATTERN = re.compile(r"^[A-Z0-9]{4}$")


def _require_exact_utc(
    value: datetime,
    *,
    field_name: str,
) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is not timezone.utc
    ):
        raise ValueError(
            f"{field_name} must use datetime.timezone.utc"
        )
    return value


def _require_package_exact_utc(
    package: HistoricalEvidencePackage,
) -> None:
    """Tighten R1.1 clocks to exact ``datetime.timezone.utc``."""

    _require_exact_utc(
        package.raw_receipt.local_received_at,
        field_name="raw_receipt.local_received_at",
    )

    for index, attachment in enumerate(
        package.attachments
    ):
        _require_exact_utc(
            attachment.local_received_at,
            field_name=(
                f"attachments[{index}].local_received_at"
            ),
        )

    _require_exact_utc(
        package.review.reviewed_at,
        field_name="review.reviewed_at",
    )

    availability = package.evidence.availability

    if availability.kind == "EXACT":
        assert availability.exact_at is not None
        _require_exact_utc(
            availability.exact_at,
            field_name="availability.exact_at",
        )
        return

    assert availability.start is not None
    assert availability.end is not None

    _require_exact_utc(
        availability.start,
        field_name="availability.start",
    )
    _require_exact_utc(
        availability.end,
        field_name="availability.end",
    )


def _require_exact_date(
    value: date,
    *,
    field_name: str,
) -> date:
    if type(value) is not date:
        raise ValueError(
            f"{field_name} must be exact date"
        )
    return value


def _require_calendar_mic(
    value: str,
) -> str:
    if (
        type(value) is not str
        or _MIC_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(
            "calendar_mic must be canonical four-character MIC"
        )
    return value


def _canonical_session(
    value: USSessionRecord,
) -> USSessionRecord:
    if type(value) is not USSessionRecord:
        raise ValueError(
            "exact USSessionRecord required"
        )

    return USSessionRecord.model_validate(
        value.model_dump(mode="python")
    )


class HistoricalUSSessionFact(BaseModel):
    """One exact US session fact and its retrospective evidence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    session: USSessionRecord
    evidence_package: HistoricalEvidencePackage

    @field_validator("session", mode="before")
    @classmethod
    def exact_session_type(
        cls,
        value,
    ):
        return _canonical_session(value)

    @field_validator(
        "evidence_package",
        mode="before",
    )
    @classmethod
    def exact_package_type(
        cls,
        value,
    ):
        if type(value) is not HistoricalEvidencePackage:
            raise ValueError(
                "exact HistoricalEvidencePackage required"
            )
        return value

    @model_validator(mode="after")
    def evidence_scope(
        self,
    ) -> "HistoricalUSSessionFact":
        covered = set(
            self.evidence_package.evidence.covered_fields
        )

        missing = (
            US_SESSION_EVIDENCE_FIELDS - covered
        )

        if missing:
            raise ValueError(
                "historical evidence does not cover "
                "required US session fields: "
                + ",".join(sorted(missing))
            )

        return self

    @property
    def identity(self) -> str:
        payload = {
            "schema_version": (
                "historical-us-session-fact-v1"
            ),
            "session": self.session.model_dump(
                mode="json"
            ),
            "evidence_package_id": (
                self.evidence_package.identity
            ),
        }

        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")

        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AdmittedUSSessionHistory:
    """Canonical complete retrospective calendar history."""

    calendar_mic: str
    coverage_start: date
    coverage_end: date
    facts: tuple[HistoricalUSSessionFact, ...]
    decision_at: datetime
    research_built_at: datetime

    @property
    def identity(self) -> str:
        # research_built_at is deliberately excluded.
        # Rebuilding the same admitted semantic history later
        # must not alter its identity.
        payload = {
            "schema_version": (
                "admitted-us-session-history-v1"
            ),
            "calendar_mic": self.calendar_mic,
            "coverage_start": (
                self.coverage_start.isoformat()
            ),
            "coverage_end": (
                self.coverage_end.isoformat()
            ),
            "decision_at": (
                self.decision_at.isoformat()
            ),
            "fact_ids": [
                item.identity
                for item in self.facts
            ],
        }

        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")

        return hashlib.sha256(encoded).hexdigest()


def _fact_sort_key(
    fact: HistoricalUSSessionFact,
):
    return (
        fact.session.market_date,
        fact.identity,
    )


def _date_range(
    start: date,
    end: date,
) -> tuple[date, ...]:
    days = (end - start).days

    return tuple(
        start + timedelta(days=offset)
        for offset in range(days + 1)
    )


def admit_us_session_history(
    facts: tuple[HistoricalUSSessionFact, ...],
    *,
    calendar_mic: str,
    coverage_start: date,
    coverage_end: date,
    decision_at: datetime,
    research_built_at: datetime,
) -> AdmittedUSSessionHistory:
    """Admit one complete retrospective MIC-scoped session history.

    Every calendar date in the inclusive coverage interval must have exactly
    one explicit session fact, including closed dates.
    """

    if type(facts) is not tuple:
        raise ValueError(
            "canonical session fact tuple required"
        )

    calendar_mic = _require_calendar_mic(
        calendar_mic
    )

    coverage_start = _require_exact_date(
        coverage_start,
        field_name="coverage_start",
    )
    coverage_end = _require_exact_date(
        coverage_end,
        field_name="coverage_end",
    )

    if coverage_end < coverage_start:
        raise ValueError(
            "coverage_end cannot precede coverage_start"
        )

    decision_at = _require_exact_utc(
        decision_at,
        field_name="decision_at",
    )
    research_built_at = _require_exact_utc(
        research_built_at,
        field_name="research_built_at",
    )

    if research_built_at < decision_at:
        raise ValueError(
            "research_built_at cannot precede decision_at"
        )

    decision_market_date = (
        decision_at
        .astimezone(
            ZoneInfo(US_MARKET_TIMEZONE)
        )
        .date()
    )

    if coverage_end > decision_market_date:
        raise ValueError(
            "coverage_end cannot exceed "
            "US-local decision date"
        )

    admitted: list[
        HistoricalUSSessionFact
    ] = []

    for item in facts:
        if type(item) is not HistoricalUSSessionFact:
            raise ValueError(
                "exact HistoricalUSSessionFact required"
            )

        session = _canonical_session(
            item.session
        )

        package = require_historical_evidence(
            item.evidence_package,
            decision_at=decision_at,
            research_built_at=research_built_at,
        )

        _require_package_exact_utc(package)

        canonical = HistoricalUSSessionFact(
            session=session,
            evidence_package=package,
        )

        if (
            canonical.session.market_date
            > decision_market_date
        ):
            raise ValueError(
                "future-effective US session fact "
                "cannot enter admitted history"
            )

        if (
            canonical.session.calendar_mic
            != calendar_mic
        ):
            raise ValueError(
                "US session fact calendar_mic "
                "does not match admitted history"
            )

        if not (
            coverage_start
            <= canonical.session.market_date
            <= coverage_end
        ):
            raise ValueError(
                "US session fact outside requested coverage"
            )

        admitted.append(canonical)

    admitted.sort(key=_fact_sort_key)

    seen_fact_ids: set[str] = set()
    by_date: dict[
        date,
        HistoricalUSSessionFact,
    ] = {}

    for item in admitted:
        if item.identity in seen_fact_ids:
            raise ValueError(
                "duplicate historical US session fact"
            )

        seen_fact_ids.add(item.identity)

        market_date = item.session.market_date

        if market_date in by_date:
            raise ValueError(
                "multiple US session facts for same "
                "calendar date"
            )

        by_date[market_date] = item

    expected_dates = _date_range(
        coverage_start,
        coverage_end,
    )

    missing = [
        item
        for item in expected_dates
        if item not in by_date
    ]

    if missing:
        raise ValueError(
            "missing explicit US session facts for "
            "calendar dates: "
            + ",".join(
                item.isoformat()
                for item in missing
            )
        )

    canonical_facts = tuple(
        by_date[item]
        for item in expected_dates
    )

    return AdmittedUSSessionHistory(
        calendar_mic=calendar_mic,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        facts=canonical_facts,
        decision_at=decision_at,
        research_built_at=research_built_at,
    )


def resolve_us_session_on_date(
    history: AdmittedUSSessionHistory,
    *,
    market_date: date,
) -> USSessionRecord:
    """Resolve one exact covered date without forward/back filling."""

    if type(history) is not AdmittedUSSessionHistory:
        raise ValueError(
            "exact AdmittedUSSessionHistory required"
        )

    market_date = _require_exact_date(
        market_date,
        field_name="market_date",
    )

    if not (
        history.coverage_start
        <= market_date
        <= history.coverage_end
    ):
        raise ValueError(
            "market_date outside admitted session coverage"
        )

    for item in history.facts:
        if item.session.market_date == market_date:
            return _canonical_session(
                item.session
            )

    raise ValueError(
        "admitted US session history is incomplete "
        "for market_date"
    )
