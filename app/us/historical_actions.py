"""US4 retrospective US corporate-action admission.

US4 admits complete bounded historical corporate-action coverage.

It deliberately does not:
- infer actions from price/volume changes;
- require an action effective date to be a trading session;
- apply price or volume transformations;
- project current ticker identity into historical dates;
- perform dividend reinvestment, merger conversion, or spinoff valuation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
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
    USCorporateActionCoverage,
    USCorporateActionEvent,
    US_MARKET_TIMEZONE,
)


US_ACTION_COVERAGE_EVIDENCE_FIELDS = frozenset(
    {
        "instrument_id",
        "coverage_start",
        "coverage_end",
        "complete",
        "actions",
    }
)


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


def _require_exact_uuid(
    value: UUID,
    *,
    field_name: str,
) -> UUID:
    if type(value) is not UUID:
        raise ValueError(
            f"{field_name} must be exact UUID"
        )
    return value


def _canonical_action(
    value: USCorporateActionEvent,
) -> USCorporateActionEvent:
    if type(value) is not USCorporateActionEvent:
        raise ValueError(
            "exact USCorporateActionEvent required"
        )

    if type(value.effective_date) is not date:
        raise ValueError(
            "corporate-action effective_date must be exact date"
        )

    return USCorporateActionEvent.model_validate(
        value.model_dump(mode="python")
    )


def _action_sort_key(
    action: USCorporateActionEvent,
):
    return (
        action.effective_date,
        action.event_id,
        action.action_type.value,
    )


def _canonical_coverage(
    value: USCorporateActionCoverage,
) -> USCorporateActionCoverage:
    if type(value) is not USCorporateActionCoverage:
        raise ValueError(
            "exact USCorporateActionCoverage required"
        )

    _require_exact_uuid(
        value.instrument_id,
        field_name="coverage.instrument_id",
    )
    _require_exact_date(
        value.coverage_start,
        field_name="coverage.coverage_start",
    )
    _require_exact_date(
        value.coverage_end,
        field_name="coverage.coverage_end",
    )

    actions = tuple(
        sorted(
            (
                _canonical_action(item)
                for item in value.actions
            ),
            key=_action_sort_key,
        )
    )

    payload = value.model_dump(mode="python")
    payload["actions"] = actions

    return USCorporateActionCoverage.model_validate(
        payload
    )


class HistoricalUSCorporateActionFact(BaseModel):
    """One complete bounded action-coverage fact and its evidence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    coverage: USCorporateActionCoverage
    evidence_package: HistoricalEvidencePackage

    @field_validator(
        "coverage",
        mode="before",
    )
    @classmethod
    def exact_coverage_type(
        cls,
        value,
    ):
        return _canonical_coverage(value)

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
    ) -> "HistoricalUSCorporateActionFact":
        covered = set(
            self.evidence_package.evidence.covered_fields
        )

        missing = (
            US_ACTION_COVERAGE_EVIDENCE_FIELDS
            - covered
        )

        if missing:
            raise ValueError(
                "historical evidence does not cover "
                "required US corporate-action fields: "
                + ",".join(sorted(missing))
            )

        return self

    @property
    def identity(self) -> str:
        payload = {
            "schema_version": (
                "historical-us-corporate-action-fact-v1"
            ),
            "coverage": self.coverage.model_dump(
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
class AdmittedUSCorporateActionHistory:
    """Complete retrospective action truth for one stable instrument."""

    instrument_id: UUID
    coverage_start: date
    coverage_end: date
    facts: tuple[HistoricalUSCorporateActionFact, ...]
    decision_at: datetime
    research_built_at: datetime

    @property
    def identity(self) -> str:
        # research_built_at is deliberately excluded:
        # rebuilding the same admitted semantic history later
        # must not change semantic identity.
        payload = {
            "schema_version": (
                "admitted-us-corporate-action-history-v1"
            ),
            "instrument_id": str(self.instrument_id),
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
    fact: HistoricalUSCorporateActionFact,
):
    return (
        fact.coverage.coverage_start,
        fact.coverage.coverage_end,
        fact.identity,
    )


def admit_us_corporate_action_history(
    facts: tuple[
        HistoricalUSCorporateActionFact,
        ...
    ],
    *,
    instrument_id: UUID,
    coverage_start: date,
    coverage_end: date,
    decision_at: datetime,
    research_built_at: datetime,
) -> AdmittedUSCorporateActionHistory:
    """Admit complete bounded retrospective corporate-action truth.

    One or more individually complete coverage segments may be supplied.
    Together they must form an exact, non-overlapping, gap-free partition
    of the requested inclusive coverage interval.
    """

    if type(facts) is not tuple:
        raise ValueError(
            "canonical corporate-action fact tuple required"
        )

    if not facts:
        raise ValueError(
            "at least one complete corporate-action "
            "coverage fact required"
        )

    instrument_id = _require_exact_uuid(
        instrument_id,
        field_name="instrument_id",
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
        HistoricalUSCorporateActionFact
    ] = []

    for item in facts:
        if type(item) is not HistoricalUSCorporateActionFact:
            raise ValueError(
                "exact HistoricalUSCorporateActionFact required"
            )

        coverage = _canonical_coverage(
            item.coverage
        )

        package = require_historical_evidence(
            item.evidence_package,
            decision_at=decision_at,
            research_built_at=research_built_at,
        )

        _require_package_exact_utc(package)

        canonical = HistoricalUSCorporateActionFact(
            coverage=coverage,
            evidence_package=package,
        )

        coverage = canonical.coverage

        if coverage.instrument_id != instrument_id:
            raise ValueError(
                "corporate-action coverage "
                "instrument_id mismatch"
            )

        if not (
            coverage_start
            <= coverage.coverage_start
            <= coverage.coverage_end
            <= coverage_end
        ):
            raise ValueError(
                "corporate-action fact outside "
                "requested coverage"
            )

        if coverage.coverage_end > decision_market_date:
            raise ValueError(
                "future-effective corporate-action "
                "coverage cannot enter admitted history"
            )

        if any(
            action.effective_date > decision_market_date
            for action in coverage.actions
        ):
            raise ValueError(
                "future-effective corporate action "
                "cannot enter admitted history"
            )

        admitted.append(canonical)

    admitted.sort(
        key=_fact_sort_key
    )

    seen_fact_ids: set[str] = set()
    seen_event_ids: set[str] = set()

    for item in admitted:
        if item.identity in seen_fact_ids:
            raise ValueError(
                "duplicate historical US "
                "corporate-action fact"
            )

        seen_fact_ids.add(item.identity)

        for action in item.coverage.actions:
            if action.event_id in seen_event_ids:
                raise ValueError(
                    "duplicate corporate-action event_id "
                    "across admitted coverage"
                )
            seen_event_ids.add(action.event_id)

    # Complete coverage means exact negative evidence matters too.
    # Segments therefore must partition every calendar date in the
    # requested interval without gaps or overlaps.
    expected_start = coverage_start

    for index, item in enumerate(admitted):
        segment = item.coverage

        if segment.coverage_start < expected_start:
            raise ValueError(
                "overlapping corporate-action coverage"
            )

        if segment.coverage_start > expected_start:
            raise ValueError(
                "gap in complete corporate-action coverage"
            )

        if segment.coverage_end == coverage_end:
            if index != len(admitted) - 1:
                raise ValueError(
                    "overlapping corporate-action coverage"
                )
            expected_start = None
            break

        expected_start = (
            segment.coverage_end
            + timedelta(days=1)
        )

    if expected_start is not None:
        raise ValueError(
            "gap in complete corporate-action coverage"
        )

    return AdmittedUSCorporateActionHistory(
        instrument_id=instrument_id,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        facts=tuple(admitted),
        decision_at=decision_at,
        research_built_at=research_built_at,
    )


def resolve_us_corporate_actions_on_date(
    history: AdmittedUSCorporateActionHistory,
    *,
    market_date: date,
) -> tuple[USCorporateActionEvent, ...]:
    """Return exact-date action truth.

    An empty tuple is meaningful complete negative evidence: the date is
    covered and no corporate actions occurred according to the admitted
    complete coverage.

    No session, ticker, price, or neighboring-date inference is performed.
    """

    if type(history) is not AdmittedUSCorporateActionHistory:
        raise ValueError(
            "exact AdmittedUSCorporateActionHistory required"
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
            "market_date outside admitted "
            "corporate-action coverage"
        )

    matching_segments = [
        item
        for item in history.facts
        if (
            item.coverage.coverage_start
            <= market_date
            <= item.coverage.coverage_end
        )
    ]

    if len(matching_segments) != 1:
        raise ValueError(
            "admitted corporate-action history "
            "is not complete and unambiguous "
            "for market_date"
        )

    actions = [
        _canonical_action(action)
        for action
        in matching_segments[0].coverage.actions
        if action.effective_date == market_date
    ]

    actions.sort(
        key=_action_sort_key
    )

    return tuple(actions)


__all__ = [
    "US_ACTION_COVERAGE_EVIDENCE_FIELDS",
    "HistoricalUSCorporateActionFact",
    "AdmittedUSCorporateActionHistory",
    "admit_us_corporate_action_history",
    "resolve_us_corporate_actions_on_date",
]
