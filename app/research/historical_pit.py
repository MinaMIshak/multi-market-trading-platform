"""R1.2 retrospective point-in-time daily research derivation.

Research-only.  This module performs no acquisition, provider access, storage,
database work, operational M3 admission, execution, or strategy validation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, localcontext
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.data.daily_canonical import (
    CanonicalDailyBar,
    DailyBarSemanticClass,
)
from app.data.reference import (
    ActionEvidence,
    ActionEvidenceRow,
    UniverseEvidence,
    UniverseMember,
)
from app.strategies.contracts import Contract

from .historical_evidence import (
    HistoricalEvidencePackage,
    require_historical_evidence,
)
from .models import _canonical


PIT_INPUT_VERSION = "historical-pit-input-v1"
PIT_OUTPUT_VERSION = "research-pit-daily-v1"
TRANSFORMATION_VERSION = "split-only-v1-decimal34"

MarketState = Literal["TRADING_SESSION", "NON_SESSION"]
InstrumentState = Literal[
    "EXPECTED_OBSERVATION",
    "SUSPENDED",
    "NOT_APPLICABLE",
    "UNSUPPORTED",
]


# These names define which exact values the R1.1 package must explicitly cover
# before the corresponding historical fact may be consumed by R1.2.
DAILY_EVIDENCE_FIELDS = (
    "canonical_symbol",
    "close",
    "high",
    "instrument_id",
    "low",
    "market_date",
    "open",
    "provider_adjusted_close_reference",
    "provider_symbol",
    "quality_flags",
    "semantic_class",
    "source_provider",
    "source_row_number",
    "source_sha256",
    "source_snapshot_date",
    "volume",
)

UNIVERSE_EVIDENCE_FIELDS = (
    "complete",
    "effective_date",
    "market",
    "members",
)

IDENTITY_EVIDENCE_FIELDS = (
    "canonical_symbol",
    "instrument_id",
    "market_date",
    "provider_symbol",
    "source_provider",
)

SESSION_EVIDENCE_FIELDS = (
    "instrument_state",
    "market_date",
    "market_state",
)

ACTION_EVIDENCE_FIELDS = (
    "actions",
    "complete",
    "coverage_end",
    "coverage_start",
    "instrument_id",
)


def _exact_date(value, *, name: str) -> date:
    if type(value) is not date:
        raise ValueError(f"{name} must be exact date")
    return value


def _exact_uuid(value, *, name: str) -> UUID:
    if type(value) is not UUID:
        raise ValueError(f"{name} must be exact UUID")
    return value


def _utc(value, *, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo != timezone.utc:
        raise ValueError(f"{name} must be canonical UTC datetime")
    return value


def _canonical_external(kind, value):
    """Reconstruct mutable/external models and reject silent normalization."""
    if type(value) is not kind:
        raise ValueError(f"canonical {kind.__name__} required")
    rebuilt = kind(
        **{
            field: getattr(value, field)
            for field in kind.model_fields
        }
    )
    if rebuilt != value:
        raise ValueError(f"noncanonical {kind.__name__}")
    return rebuilt


def _canonical_bar(value) -> CanonicalDailyBar:
    return _canonical_external(CanonicalDailyBar, value)


def _canonical_member(value) -> UniverseMember:
    return _canonical_external(UniverseMember, value)


def _canonical_universe(value) -> UniverseEvidence:
    if type(value) is not UniverseEvidence:
        raise ValueError("canonical UniverseEvidence required")
    if type(value.members) is not tuple:
        raise ValueError("canonical universe member tuple required")

    members = tuple(
        _canonical_member(member)
        for member in value.members
    )

    rebuilt = UniverseEvidence(
        contract=value.contract,
        market=value.market,
        effective_date=value.effective_date,
        published_at=value.published_at,
        complete=value.complete,
        members=members,
    )
    if rebuilt != value:
        raise ValueError("noncanonical UniverseEvidence")

    ordered = tuple(
        sorted(
            members,
            key=lambda member: (
                str(member.instrument_id),
                member.symbol,
            ),
        )
    )

    if ordered == members:
        return rebuilt

    return UniverseEvidence(
        contract=rebuilt.contract,
        market=rebuilt.market,
        effective_date=rebuilt.effective_date,
        published_at=rebuilt.published_at,
        complete=rebuilt.complete,
        members=ordered,
    )


def _canonical_action_row(value) -> ActionEvidenceRow:
    return _canonical_external(ActionEvidenceRow, value)


def _canonical_actions(value) -> ActionEvidence:
    if type(value) is not ActionEvidence:
        raise ValueError("canonical ActionEvidence required")
    if type(value.actions) is not tuple:
        raise ValueError("canonical action tuple required")

    rows = tuple(
        _canonical_action_row(row)
        for row in value.actions
    )

    rebuilt = ActionEvidence(
        contract=value.contract,
        instrument_id=value.instrument_id,
        coverage_start=value.coverage_start,
        coverage_end=value.coverage_end,
        published_at=value.published_at,
        complete=value.complete,
        actions=rows,
    )
    if rebuilt != value:
        raise ValueError("noncanonical ActionEvidence")

    ordered = tuple(
        sorted(
            rows,
            key=lambda row: (
                row.effective_date,
                row.event_id,
            ),
        )
    )

    if ordered == rows:
        return rebuilt

    return ActionEvidence(
        contract=rebuilt.contract,
        instrument_id=rebuilt.instrument_id,
        coverage_start=rebuilt.coverage_start,
        coverage_end=rebuilt.coverage_end,
        published_at=rebuilt.published_at,
        complete=rebuilt.complete,
        actions=ordered,
    )


def _canonical_package(value) -> HistoricalEvidencePackage:
    canonical = _canonical(HistoricalEvidencePackage, value)
    if canonical != value:
        raise ValueError("noncanonical HistoricalEvidencePackage")
    return canonical


def _package_latest(package: HistoricalEvidencePackage) -> datetime:
    availability = package.evidence.availability
    if availability.kind == "EXACT":
        assert availability.exact_at is not None
        return availability.exact_at
    assert availability.end is not None
    return availability.end


def _available_by(
    package: HistoricalEvidencePackage,
    decision_at: datetime,
) -> bool:
    return _package_latest(package) <= decision_at


def _require_fields(
    package: HistoricalEvidencePackage,
    required: tuple[str, ...],
) -> None:
    covered = set(package.evidence.covered_fields)
    missing = set(required) - covered
    if missing:
        raise ValueError(
            "historical availability evidence does not cover required fields: "
            + ",".join(sorted(missing))
        )


def _admit_package(
    package: HistoricalEvidencePackage,
    *,
    decision_at: datetime,
    research_built_at: datetime,
    required_fields: tuple[str, ...],
) -> HistoricalEvidencePackage:
    admitted = require_historical_evidence(
        package,
        decision_at=decision_at,
        research_built_at=research_built_at,
    )
    _require_fields(admitted, required_fields)
    return admitted


def _stable_json(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _model_json(value):
    return value.model_dump(mode="json")


def _unique_exact(items, *, label: str) -> tuple:
    encoded = [_stable_json(_model_json(item)) for item in items]
    if len(encoded) != len(set(encoded)):
        raise ValueError(f"duplicate {label} candidate")
    return items


class HistoricalDailyObservation(Contract):
    schema_version: Literal[
        "historical-daily-observation-v1"
    ] = "historical-daily-observation-v1"

    row: CanonicalDailyBar
    evidence: HistoricalEvidencePackage

    @field_validator("row", mode="before")
    @classmethod
    def canonical_row(cls, value):
        return _canonical_bar(value)

    @field_validator("evidence", mode="before")
    @classmethod
    def canonical_evidence(cls, value):
        return _canonical_package(value)


class HistoricalUniverseSnapshot(Contract):
    schema_version: Literal[
        "historical-universe-snapshot-v1"
    ] = "historical-universe-snapshot-v1"

    universe: UniverseEvidence
    evidence: HistoricalEvidencePackage

    @field_validator("universe", mode="before")
    @classmethod
    def canonical_universe(cls, value):
        return _canonical_universe(value)

    @field_validator("evidence", mode="before")
    @classmethod
    def canonical_evidence(cls, value):
        return _canonical_package(value)


class HistoricalIdentityMapping(Contract):
    schema_version: Literal[
        "historical-identity-mapping-v1"
    ] = "historical-identity-mapping-v1"

    market_date: date
    instrument_id: UUID
    canonical_symbol: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z0-9][A-Z0-9._-]{0,63}$",
    )
    provider_symbol: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z0-9][A-Z0-9._-]{0,63}$",
    )
    source_provider: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$",
    )
    evidence: HistoricalEvidencePackage

    @field_validator("evidence", mode="before")
    @classmethod
    def canonical_evidence(cls, value):
        return _canonical_package(value)


class HistoricalSessionRecord(Contract):
    schema_version: Literal[
        "historical-session-record-v1"
    ] = "historical-session-record-v1"

    market_date: date
    market_state: MarketState
    instrument_state: InstrumentState
    evidence: HistoricalEvidencePackage

    @field_validator("evidence", mode="before")
    @classmethod
    def canonical_evidence(cls, value):
        return _canonical_package(value)

    @model_validator(mode="after")
    def shape(self):
        if self.market_state == "NON_SESSION":
            if self.instrument_state != "NOT_APPLICABLE":
                raise ValueError(
                    "NON_SESSION requires NOT_APPLICABLE"
                )
        else:
            if self.instrument_state == "NOT_APPLICABLE":
                raise ValueError(
                    "TRADING_SESSION cannot use NOT_APPLICABLE"
                )
        return self


class HistoricalActionCoverage(Contract):
    schema_version: Literal[
        "historical-action-coverage-v1"
    ] = "historical-action-coverage-v1"

    actions: ActionEvidence
    evidence: HistoricalEvidencePackage

    @field_validator("actions", mode="before")
    @classmethod
    def canonical_actions(cls, value):
        return _canonical_actions(value)

    @field_validator("evidence", mode="before")
    @classmethod
    def canonical_evidence(cls, value):
        return _canonical_package(value)


def _canonical_own(kind, value):
    canonical = _canonical(kind, value)
    if canonical != value:
        raise ValueError(f"noncanonical {kind.__name__}")
    return canonical


class HistoricalPITInput(Contract):
    schema_version: Literal[
        "historical-pit-input-v1"
    ] = PIT_INPUT_VERSION

    observations: tuple[HistoricalDailyObservation, ...]
    universes: tuple[HistoricalUniverseSnapshot, ...]
    identities: tuple[HistoricalIdentityMapping, ...]
    sessions: tuple[HistoricalSessionRecord, ...]
    action_coverage: HistoricalActionCoverage

    @field_validator("observations", mode="before")
    @classmethod
    def canonical_observations(cls, value):
        if type(value) is not tuple:
            raise ValueError("canonical observation tuple required")
        items = tuple(
            _canonical_own(HistoricalDailyObservation, item)
            for item in value
        )
        _unique_exact(items, label="daily observation")
        return tuple(
            sorted(
                items,
                key=lambda item: (
                    item.row.market_date,
                    str(item.row.instrument_id),
                    item.evidence.identity,
                ),
            )
        )

    @field_validator("universes", mode="before")
    @classmethod
    def canonical_universes(cls, value):
        if type(value) is not tuple:
            raise ValueError("canonical universe tuple required")
        items = tuple(
            _canonical_own(HistoricalUniverseSnapshot, item)
            for item in value
        )
        _unique_exact(items, label="universe")
        return tuple(
            sorted(
                items,
                key=lambda item: (
                    item.universe.effective_date,
                    item.evidence.identity,
                ),
            )
        )

    @field_validator("identities", mode="before")
    @classmethod
    def canonical_identities(cls, value):
        if type(value) is not tuple:
            raise ValueError("canonical identity tuple required")
        items = tuple(
            _canonical_own(HistoricalIdentityMapping, item)
            for item in value
        )
        _unique_exact(items, label="identity")
        return tuple(
            sorted(
                items,
                key=lambda item: (
                    item.market_date,
                    str(item.instrument_id),
                    item.source_provider,
                    item.provider_symbol,
                    item.evidence.identity,
                ),
            )
        )

    @field_validator("sessions", mode="before")
    @classmethod
    def canonical_sessions(cls, value):
        if type(value) is not tuple:
            raise ValueError("canonical session tuple required")
        items = tuple(
            _canonical_own(HistoricalSessionRecord, item)
            for item in value
        )
        _unique_exact(items, label="session")
        return tuple(
            sorted(
                items,
                key=lambda item: (
                    item.market_date,
                    item.evidence.identity,
                ),
            )
        )

    @field_validator("action_coverage", mode="before")
    @classmethod
    def canonical_action_coverage(cls, value):
        return _canonical_own(HistoricalActionCoverage, value)


class ResearchIndicatorBar(Contract):
    schema_version: Literal[
        "research-indicator-bar-v1"
    ] = "research-indicator-bar-v1"

    market_date: date
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    price_factor: Decimal = Field(gt=0)
    volume_factor: Decimal = Field(gt=0)
    event_ids: tuple[str, ...]
    transformation_version: Literal[
        "split-only-v1-decimal34"
    ] = TRANSFORMATION_VERSION

    @field_validator("event_ids", mode="before")
    @classmethod
    def canonical_event_ids(cls, value):
        if type(value) is not tuple:
            raise ValueError("canonical event-id tuple required")
        if len(value) != len(set(value)):
            raise ValueError("duplicate event id")
        return value

    @model_validator(mode="after")
    def prices(self):
        if self.high < self.low:
            raise ValueError("high cannot be below low")
        if not self.low <= self.open <= self.high:
            raise ValueError("open outside high-low range")
        if not self.low <= self.close <= self.high:
            raise ValueError("close outside high-low range")
        return self


class ResearchPITDaily(Contract):
    version: Literal[
        "research-pit-daily-v1"
    ] = PIT_OUTPUT_VERSION

    instrument_id: UUID
    canonical_symbol: str | None
    start_date: date
    decision_date: date
    decision_at: datetime
    research_built_at: datetime

    raw_rows: tuple[CanonicalDailyBar, ...]
    indicator_rows: tuple[ResearchIndicatorBar, ...]

    used_evidence_ids: tuple[str, ...]
    derivation_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("raw_rows", mode="before")
    @classmethod
    def canonical_raw_rows(cls, value):
        if type(value) is not tuple:
            raise ValueError("canonical raw-row tuple required")
        return tuple(_canonical_bar(row) for row in value)

    @field_validator("indicator_rows", mode="before")
    @classmethod
    def canonical_indicator_rows(cls, value):
        if type(value) is not tuple:
            raise ValueError("canonical indicator-row tuple required")
        return tuple(
            _canonical_own(ResearchIndicatorBar, row)
            for row in value
        )

    @field_validator("used_evidence_ids", mode="before")
    @classmethod
    def canonical_evidence_ids(cls, value):
        if type(value) is not tuple:
            raise ValueError("canonical evidence-id tuple required")
        if len(value) != len(set(value)):
            raise ValueError("duplicate used evidence identity")
        if tuple(sorted(value)) != value:
            raise ValueError("used evidence identities must be sorted")
        return value


def _date_range(start: date, end: date) -> tuple[date, ...]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return tuple(days)


def derive_research_pit_daily(
    source: HistoricalPITInput,
    *,
    instrument_id: UUID,
    start_date: date,
    decision_date: date,
    decision_at: datetime,
    research_built_at: datetime,
) -> ResearchPITDaily:
    """Derive one deterministic retrospective EGX PIT daily research prefix."""
    source = _canonical_own(HistoricalPITInput, source)
    instrument_id = _exact_uuid(
        instrument_id,
        name="instrument_id",
    )
    start_date = _exact_date(start_date, name="start_date")
    decision_date = _exact_date(
        decision_date,
        name="decision_date",
    )
    decision_at = _utc(decision_at, name="decision_at")
    research_built_at = _utc(
        research_built_at,
        name="research_built_at",
    )

    if start_date > decision_date:
        raise ValueError("start_date cannot follow decision_date")
    if research_built_at < decision_at:
        raise ValueError(
            "research_built_at cannot precede decision_at"
        )

    requested_dates = _date_range(start_date, decision_date)
    requested_set = set(requested_dates)

    # Structural canonical validation happened above for every candidate,
    # including future candidates. Availability alone decides which candidates
    # may enter this historical prefix.
    admitted_sessions = [
        item
        for item in source.sessions
        if item.market_date in requested_set
        and _available_by(item.evidence, decision_at)
    ]

    session_by_date: dict[date, HistoricalSessionRecord] = {}
    used_packages: list[HistoricalEvidencePackage] = []
    used_sessions: list[HistoricalSessionRecord] = []

    for day in requested_dates:
        matches = [
            item
            for item in admitted_sessions
            if item.market_date == day
        ]
        if len(matches) != 1:
            raise ValueError(
                "exactly one admitted session record required "
                f"for {day}"
            )
        session = matches[0]
        if session.instrument_state == "UNSUPPORTED":
            raise ValueError("unsupported session state")
        _admit_package(
            session.evidence,
            decision_at=decision_at,
            research_built_at=research_built_at,
            required_fields=SESSION_EVIDENCE_FIELDS,
        )
        session_by_date[day] = session
        used_sessions.append(session)
        used_packages.append(session.evidence)

    trading_dates = {
        day
        for day, session in session_by_date.items()
        if session.market_state == "TRADING_SESSION"
    }

    admitted_universes = [
        item
        for item in source.universes
        if item.universe.effective_date in trading_dates
        and _available_by(item.evidence, decision_at)
    ]

    universe_by_date: dict[date, HistoricalUniverseSnapshot] = {}
    for day in sorted(trading_dates):
        matches = [
            item
            for item in admitted_universes
            if item.universe.effective_date == day
        ]
        if len(matches) != 1:
            raise ValueError(
                "exactly one admitted dated universe required "
                f"for {day}"
            )
        snapshot = matches[0]
        universe = snapshot.universe
        if universe.market != "EGX":
            raise ValueError("historical universe market must be EGX")
        if universe.effective_date != day:
            raise ValueError("historical universe date mismatch")
        if universe.complete is not True:
            raise ValueError("complete dated universe required")

        _admit_package(
            snapshot.evidence,
            decision_at=decision_at,
            research_built_at=research_built_at,
            required_fields=UNIVERSE_EVIDENCE_FIELDS,
        )
        universe_by_date[day] = snapshot
        used_packages.append(snapshot.evidence)

    admitted_identities = [
        item
        for item in source.identities
        if item.market_date in trading_dates
        and _available_by(item.evidence, decision_at)
    ]

    # A dated provider symbol cannot resolve to multiple stable instruments.
    provider_keys: dict[tuple, UUID] = {}
    for mapping in admitted_identities:
        key = (
            mapping.market_date,
            mapping.source_provider,
            mapping.provider_symbol,
        )
        previous = provider_keys.get(key)
        if previous is not None and previous != mapping.instrument_id:
            raise ValueError(
                "ambiguous dated provider-symbol identity mapping"
            )
        provider_keys[key] = mapping.instrument_id

    identity_by_date: dict[date, HistoricalIdentityMapping] = {}
    for day in sorted(trading_dates):
        matches = [
            item
            for item in admitted_identities
            if item.market_date == day
            and item.instrument_id == instrument_id
        ]
        if len(matches) != 1:
            raise ValueError(
                "exactly one admitted dated identity required "
                f"for {day}"
            )
        mapping = matches[0]
        _admit_package(
            mapping.evidence,
            decision_at=decision_at,
            research_built_at=research_built_at,
            required_fields=IDENTITY_EVIDENCE_FIELDS,
        )
        if (
            mapping.evidence.raw_receipt.provider
            != mapping.source_provider
        ):
            raise ValueError(
                "identity evidence provider mismatch"
            )
        identity_by_date[day] = mapping
        used_packages.append(mapping.evidence)

    admitted_observations = [
        item
        for item in source.observations
        if item.row.instrument_id == instrument_id
        and item.row.market_date in requested_set
        and _available_by(item.evidence, decision_at)
    ]

    observations_by_date: dict[date, HistoricalDailyObservation] = {}
    for item in admitted_observations:
        day = item.row.market_date
        if day in observations_by_date:
            raise ValueError(
                "duplicate/conflicting admitted daily observation"
            )
        observations_by_date[day] = item

    used_universes: list[HistoricalUniverseSnapshot] = []
    used_identities: list[HistoricalIdentityMapping] = []
    raw_rows: list[CanonicalDailyBar] = []

    for day in requested_dates:
        session = session_by_date[day]
        observation = observations_by_date.get(day)

        if session.market_state == "NON_SESSION":
            if session.instrument_state != "NOT_APPLICABLE":
                raise ValueError(
                    "NON_SESSION requires NOT_APPLICABLE"
                )
            if observation is not None:
                raise ValueError(
                    "daily observation present on NON_SESSION"
                )
            continue

        if session.instrument_state == "NOT_APPLICABLE":
            raise ValueError(
                "TRADING_SESSION cannot use NOT_APPLICABLE"
            )
        if session.instrument_state == "UNSUPPORTED":
            raise ValueError("unsupported session state")

        snapshot = universe_by_date[day]
        mapping = identity_by_date[day]

        members = [
            member
            for member in snapshot.universe.members
            if member.instrument_id == instrument_id
        ]
        if len(members) != 1:
            raise ValueError(
                "target instrument absent from dated universe"
            )
        member = members[0]
        if not member.eligible:
            raise ValueError(
                "target instrument is ineligible in dated universe"
            )
        if member.symbol != mapping.canonical_symbol:
            raise ValueError(
                "dated universe symbol does not match identity"
            )

        used_universes.append(snapshot)
        used_identities.append(mapping)

        if session.instrument_state == "SUSPENDED":
            if observation is not None:
                raise ValueError(
                    "SUSPENDED session must not contain daily bar"
                )
            continue

        if session.instrument_state != "EXPECTED_OBSERVATION":
            raise ValueError("unsupported instrument session state")

        if observation is None:
            raise ValueError(
                "EXPECTED_OBSERVATION requires daily bar"
            )

        row = observation.row
        if row.semantic_class != DailyBarSemanticClass.VALID_EXECUTABLE:
            raise ValueError(
                "quarantined daily observation is not admissible"
            )
        if row.instrument_id != instrument_id:
            raise ValueError("daily instrument mismatch")
        if row.market_date != day:
            raise ValueError("daily market date mismatch")
        if row.canonical_symbol != mapping.canonical_symbol:
            raise ValueError("daily canonical symbol mismatch")
        if row.provider_symbol != mapping.provider_symbol:
            raise ValueError("daily provider symbol mismatch")
        if row.source_provider != mapping.source_provider:
            raise ValueError("daily source provider mismatch")

        _admit_package(
            observation.evidence,
            decision_at=decision_at,
            research_built_at=research_built_at,
            required_fields=DAILY_EVIDENCE_FIELDS,
        )

        if (
            observation.evidence.raw_receipt.provider
            != row.source_provider
        ):
            raise ValueError(
                "daily evidence provider mismatch"
            )
        if (
            observation.evidence.raw_receipt.sha256
            != row.source_sha256
        ):
            raise ValueError(
                "daily evidence hash mismatch"
            )

        used_packages.append(observation.evidence)

        # Output gets a separately reconstructed object. Input is never mutated.
        raw_rows.append(_canonical_bar(row))

    raw_rows.sort(key=lambda row: row.market_date)

    coverage = source.action_coverage
    if not _available_by(coverage.evidence, decision_at):
        raise ValueError(
            "exact action coverage unavailable by decision"
        )

    actions = coverage.actions
    if actions.instrument_id != instrument_id:
        raise ValueError("action coverage instrument mismatch")
    if actions.coverage_start != start_date:
        raise ValueError("exact action coverage start required")
    if actions.coverage_end != decision_date:
        raise ValueError("exact action coverage end required")
    if actions.complete is not True:
        raise ValueError("complete action coverage required")

    _admit_package(
        coverage.evidence,
        decision_at=decision_at,
        research_built_at=research_built_at,
        required_fields=ACTION_EVIDENCE_FIELDS,
    )
    used_packages.append(coverage.evidence)

    relevant_actions = tuple(
        sorted(
            (
                action
                for action in actions.actions
                if start_date
                <= action.effective_date
                <= decision_date
            ),
            key=lambda action: (
                action.effective_date,
                action.event_id,
            ),
        )
    )

    if len({a.event_id for a in relevant_actions}) != len(
        relevant_actions
    ):
        raise ValueError("duplicate corporate-action event identity")

    if any(
        action.action_type != "SPLIT"
        for action in relevant_actions
    ):
        raise ValueError(
            "unsupported corporate action requires explicit semantics"
        )

    indicator_rows: list[ResearchIndicatorBar] = []

    with localcontext(Context(prec=34)):
        for bar in raw_rows:
            assert bar.open is not None
            assert bar.high is not None
            assert bar.low is not None
            assert bar.close is not None
            assert bar.volume is not None

            events = tuple(
                action
                for action in relevant_actions
                if bar.market_date < action.effective_date
            )

            price_factor = Decimal(1)
            volume_factor = Decimal(1)

            for event in events:
                assert event.old_shares is not None
                assert event.new_shares is not None
                price_factor *= (
                    event.old_shares / event.new_shares
                )
                volume_factor *= (
                    event.new_shares / event.old_shares
                )

            if price_factor <= 0 or volume_factor <= 0:
                raise ValueError("unrepresentable split factor")

            indicator_rows.append(
                ResearchIndicatorBar(
                    market_date=bar.market_date,
                    open=bar.open * price_factor,
                    high=bar.high * price_factor,
                    low=bar.low * price_factor,
                    close=bar.close * price_factor,
                    volume=bar.volume * volume_factor,
                    price_factor=price_factor,
                    volume_factor=volume_factor,
                    event_ids=tuple(
                        event.event_id
                        for event in events
                    ),
                )
            )

    canonical_symbol = None
    if trading_dates:
        latest_trading_date = max(trading_dates)
        canonical_symbol = identity_by_date[
            latest_trading_date
        ].canonical_symbol

    used_evidence_ids = tuple(
        sorted(
            {
                package.identity
                for package in used_packages
            }
        )
    )

    derivation_payload = {
        "version": PIT_OUTPUT_VERSION,
        "input_version": PIT_INPUT_VERSION,
        "transformation_version": TRANSFORMATION_VERSION,
        "instrument_id": str(instrument_id),
        "start_date": start_date.isoformat(),
        "decision_date": decision_date.isoformat(),
        "decision_at": decision_at.isoformat(),
        "raw_rows": [
            row.model_dump(mode="json")
            for row in raw_rows
        ],
        "indicator_rows": [
            row.model_dump(mode="json")
            for row in indicator_rows
        ],
        "sessions": [
            {
                "market_date": row.market_date.isoformat(),
                "market_state": row.market_state,
                "instrument_state": row.instrument_state,
                "evidence_id": row.evidence.identity,
            }
            for row in used_sessions
        ],
        "universes": [
            {
                "universe": row.universe.model_dump(mode="json"),
                "evidence_id": row.evidence.identity,
            }
            for row in used_universes
        ],
        "identities": [
            {
                "market_date": row.market_date.isoformat(),
                "instrument_id": str(row.instrument_id),
                "canonical_symbol": row.canonical_symbol,
                "provider_symbol": row.provider_symbol,
                "source_provider": row.source_provider,
                "evidence_id": row.evidence.identity,
            }
            for row in used_identities
        ],
        "action_coverage": {
            "actions": actions.model_dump(mode="json"),
            "evidence_id": coverage.evidence.identity,
        },
        "used_evidence_ids": list(used_evidence_ids),
    }

    derivation_id = hashlib.sha256(
        _stable_json(derivation_payload).encode("utf-8")
    ).hexdigest()

    return ResearchPITDaily(
        instrument_id=instrument_id,
        canonical_symbol=canonical_symbol,
        start_date=start_date,
        decision_date=decision_date,
        decision_at=decision_at,
        research_built_at=research_built_at,
        raw_rows=tuple(raw_rows),
        indicator_rows=tuple(indicator_rows),
        used_evidence_ids=used_evidence_ids,
        derivation_id=derivation_id,
    )
