"""US5B retrospective point-in-time daily dataset assembly.

This module composes the independent US historical evidence boundaries:

US1 listing identity
US2 explicit sessions
US3 raw daily OHLCV
US4 complete corporate actions
US5A exact-date universe snapshots

The result preserves raw executable observations and separately derives an
indicator-only split-normalized series using explicit split evidence.

No provider, network, database, broker, or production integration occurs here.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import (
    Context,
    Decimal,
    DecimalException,
    localcontext,
)
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from app.us.contracts import (
    USCorporateActionEvent,
    USCorporateActionType,
    USSessionState,
    US_MARKET_TIMEZONE,
)
from app.us.historical_actions import (
    AdmittedUSCorporateActionHistory,
    admit_us_corporate_action_history,
    resolve_us_corporate_actions_on_date,
)
from app.us.historical_daily import (
    AdmittedUSDailyBarHistory,
    USHistoricalDailyBar,
    admit_us_daily_bar_history,
    resolve_us_daily_bar_on_date,
)
from app.us.historical_identity import (
    AdmittedUSListingHistory,
    admit_us_listing_history,
    resolve_us_listing_on_date,
)
from app.us.historical_session import (
    AdmittedUSSessionHistory,
    admit_us_session_history,
    resolve_us_session_on_date,
)
from app.us.historical_universe import (
    AdmittedUSUniverseHistory,
    admit_us_universe_history,
    resolve_us_universe_on_date,
)


_MIC_RE = re.compile(r"^[A-Z0-9]{4}$")

US_PIT_TRANSFORMATION = "split-only-v1-decimal34"


class USPITExclusionReason(StrEnum):
    CLOSED_SESSION = "CLOSED_SESSION"
    NOT_UNIVERSE_MEMBER = "NOT_UNIVERSE_MEMBER"
    INELIGIBLE_UNIVERSE_MEMBER = "INELIGIBLE_UNIVERSE_MEMBER"


@dataclass(frozen=True)
class USPITDateExclusion:
    market_date: date
    reason: USPITExclusionReason


@dataclass(frozen=True)
class USSplitAdjustedObservation:
    """Indicator-only split-normalized values; never execution prices."""

    instrument_id: UUID
    market_date: date
    canonical_symbol: str
    listing_mic: str

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    price_factor: Decimal
    volume_factor: Decimal
    split_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class USRetrospectivePITDailyDataset:
    """PIT-safe retrospective daily research input for one stable instrument."""

    instrument_id: UUID
    calendar_mic: str
    coverage_start: date
    coverage_end: date

    rows: tuple[USHistoricalDailyBar, ...]
    split_adjusted: tuple[USSplitAdjustedObservation, ...]
    exclusions: tuple[USPITDateExclusion, ...]

    transformation: str

    listing_history_id: str
    session_history_id: str
    daily_history_id: str
    action_history_id: str
    universe_history_id: str

    decision_at: datetime
    research_built_at: datetime

    dq_status: str = "VALIDATED"

    @property
    def identity(self) -> str:
        # research_built_at is deliberately excluded from semantic identity.
        payload = {
            "schema_version": "us-retrospective-pit-daily-v1",
            "instrument_id": str(self.instrument_id),
            "calendar_mic": self.calendar_mic,
            "coverage_start": self.coverage_start.isoformat(),
            "coverage_end": self.coverage_end.isoformat(),
            "transformation": self.transformation,
            "decision_at": self.decision_at.isoformat(),
            "listing_history_id": self.listing_history_id,
            "session_history_id": self.session_history_id,
            "daily_history_id": self.daily_history_id,
            "action_history_id": self.action_history_id,
            "universe_history_id": self.universe_history_id,
            "rows": [
                row.model_dump(mode="json")
                for row in self.rows
            ],
            "split_adjusted": [
                {
                    "instrument_id": str(row.instrument_id),
                    "market_date": row.market_date.isoformat(),
                    "canonical_symbol": row.canonical_symbol,
                    "listing_mic": row.listing_mic,
                    "open": str(row.open),
                    "high": str(row.high),
                    "low": str(row.low),
                    "close": str(row.close),
                    "volume": str(row.volume),
                    "price_factor": str(row.price_factor),
                    "volume_factor": str(row.volume_factor),
                    "split_event_ids": list(row.split_event_ids),
                }
                for row in self.split_adjusted
            ],
            "exclusions": [
                {
                    "market_date": item.market_date.isoformat(),
                    "reason": item.reason.value,
                }
                for item in self.exclusions
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


def _require_calendar_mic(
    value: str,
) -> str:
    if (
        type(value) is not str
        or _MIC_RE.fullmatch(value) is None
    ):
        raise ValueError(
            "calendar_mic must be canonical four-character MIC"
        )
    return value


def _calendar_dates(
    start: date,
    end: date,
) -> tuple[date, ...]:
    count = (end - start).days

    return tuple(
        start + timedelta(days=offset)
        for offset in range(count + 1)
    )


def _canonical_listing_history(
    value: AdmittedUSListingHistory,
) -> AdmittedUSListingHistory:
    if type(value) is not AdmittedUSListingHistory:
        raise ValueError(
            "exact AdmittedUSListingHistory required"
        )

    canonical = admit_us_listing_history(
        value.facts,
        decision_at=value.decision_at,
        research_built_at=value.research_built_at,
    )

    if canonical.identity != value.identity:
        raise ValueError(
            "noncanonical admitted US listing history"
        )

    return canonical


def _canonical_session_history(
    value: AdmittedUSSessionHistory,
) -> AdmittedUSSessionHistory:
    if type(value) is not AdmittedUSSessionHistory:
        raise ValueError(
            "exact AdmittedUSSessionHistory required"
        )

    canonical = admit_us_session_history(
        value.facts,
        calendar_mic=value.calendar_mic,
        coverage_start=value.coverage_start,
        coverage_end=value.coverage_end,
        decision_at=value.decision_at,
        research_built_at=value.research_built_at,
    )

    if canonical.identity != value.identity:
        raise ValueError(
            "noncanonical admitted US session history"
        )

    return canonical


def _canonical_action_history(
    value: AdmittedUSCorporateActionHistory,
) -> AdmittedUSCorporateActionHistory:
    if type(value) is not AdmittedUSCorporateActionHistory:
        raise ValueError(
            "exact AdmittedUSCorporateActionHistory required"
        )

    canonical = admit_us_corporate_action_history(
        value.facts,
        instrument_id=value.instrument_id,
        coverage_start=value.coverage_start,
        coverage_end=value.coverage_end,
        decision_at=value.decision_at,
        research_built_at=value.research_built_at,
    )

    if canonical.identity != value.identity:
        raise ValueError(
            "noncanonical admitted US corporate-action history"
        )

    return canonical


def _canonical_universe_history(
    value: AdmittedUSUniverseHistory,
) -> AdmittedUSUniverseHistory:
    if type(value) is not AdmittedUSUniverseHistory:
        raise ValueError(
            "exact AdmittedUSUniverseHistory required"
        )

    canonical = admit_us_universe_history(
        value.facts,
        decision_at=value.decision_at,
        research_built_at=value.research_built_at,
    )

    if canonical.identity != value.identity:
        raise ValueError(
            "noncanonical admitted US universe history"
        )

    return canonical


def _canonical_daily_history(
    value: AdmittedUSDailyBarHistory,
    *,
    listing_history: AdmittedUSListingHistory,
    session_history: AdmittedUSSessionHistory,
) -> AdmittedUSDailyBarHistory:
    if type(value) is not AdmittedUSDailyBarHistory:
        raise ValueError(
            "exact AdmittedUSDailyBarHistory required"
        )

    canonical = admit_us_daily_bar_history(
        value.facts,
        instrument_id=value.instrument_id,
        calendar_mic=value.calendar_mic,
        coverage_start=value.coverage_start,
        coverage_end=value.coverage_end,
        listing_history=listing_history,
        session_history=session_history,
        decision_at=value.decision_at,
        research_built_at=value.research_built_at,
    )

    if canonical.identity != value.identity:
        raise ValueError(
            "noncanonical admitted US daily-bar history"
        )

    return canonical


def _require_dependency_horizon(
    *,
    name: str,
    history,
    decision_at: datetime,
    research_built_at: datetime,
) -> None:
    if history.decision_at != decision_at:
        raise ValueError(
            f"{name} decision_at must match PIT decision_at"
        )

    if history.research_built_at > research_built_at:
        raise ValueError(
            f"{name} was built after PIT research build"
        )


def _action_sort_key(
    action: USCorporateActionEvent,
):
    return (
        action.effective_date,
        action.event_id,
        action.action_type.value,
    )


def _series_crosses_event(
    dates: tuple[date, ...],
    event_date: date,
) -> bool:
    return (
        any(item < event_date for item in dates)
        and any(item >= event_date for item in dates)
    )


def build_us_retrospective_pit_daily_dataset(
    *,
    instrument_id: UUID,
    calendar_mic: str,
    coverage_start: date,
    coverage_end: date,
    listing_history: AdmittedUSListingHistory,
    session_history: AdmittedUSSessionHistory,
    daily_history: AdmittedUSDailyBarHistory,
    action_history: AdmittedUSCorporateActionHistory,
    universe_history: AdmittedUSUniverseHistory,
    decision_at: datetime,
    research_built_at: datetime,
) -> USRetrospectivePITDailyDataset:
    """Compose one PIT-safe retrospective US daily research dataset."""

    instrument_id = _require_exact_uuid(
        instrument_id,
        field_name="instrument_id",
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

    listing_history = _canonical_listing_history(
        listing_history
    )
    session_history = _canonical_session_history(
        session_history
    )
    universe_history = _canonical_universe_history(
        universe_history
    )
    action_history = _canonical_action_history(
        action_history
    )

    daily_history = _canonical_daily_history(
        daily_history,
        listing_history=listing_history,
        session_history=session_history,
    )

    dependencies = (
        ("listing history", listing_history),
        ("session history", session_history),
        ("daily history", daily_history),
        ("corporate-action history", action_history),
        ("universe history", universe_history),
    )

    for name, history in dependencies:
        _require_dependency_horizon(
            name=name,
            history=history,
            decision_at=decision_at,
            research_built_at=research_built_at,
        )

    if session_history.calendar_mic != calendar_mic:
        raise ValueError(
            "session history calendar_mic mismatch"
        )

    if daily_history.calendar_mic != calendar_mic:
        raise ValueError(
            "daily history calendar_mic mismatch"
        )

    if daily_history.instrument_id != instrument_id:
        raise ValueError(
            "daily history instrument_id mismatch"
        )

    if action_history.instrument_id != instrument_id:
        raise ValueError(
            "corporate-action history instrument_id mismatch"
        )

    if not (
        session_history.coverage_start
        <= coverage_start
        <= coverage_end
        <= session_history.coverage_end
    ):
        raise ValueError(
            "session history does not cover PIT interval"
        )

    if not (
        daily_history.coverage_start
        <= coverage_start
        <= coverage_end
        <= daily_history.coverage_end
    ):
        raise ValueError(
            "daily history does not cover PIT interval"
        )

    if not (
        action_history.coverage_start
        <= coverage_start
        <= coverage_end
        <= action_history.coverage_end
    ):
        raise ValueError(
            "corporate-action history does not cover PIT interval"
        )

    raw_rows: list[USHistoricalDailyBar] = []
    exclusions: list[USPITDateExclusion] = []

    listing_by_date = {}

    for market_date in _calendar_dates(
        coverage_start,
        coverage_end,
    ):
        session = resolve_us_session_on_date(
            session_history,
            market_date=market_date,
        )

        if session.calendar_mic != calendar_mic:
            raise ValueError(
                "resolved session calendar_mic mismatch"
            )

        if session.state == USSessionState.CLOSED:
            exclusions.append(
                USPITDateExclusion(
                    market_date=market_date,
                    reason=(
                        USPITExclusionReason
                        .CLOSED_SESSION
                    ),
                )
            )
            continue

        snapshot = resolve_us_universe_on_date(
            universe_history,
            effective_date=market_date,
        )

        members = [
            member
            for member in snapshot.members
            if member.instrument_id == instrument_id
        ]

        if not members:
            exclusions.append(
                USPITDateExclusion(
                    market_date=market_date,
                    reason=(
                        USPITExclusionReason
                        .NOT_UNIVERSE_MEMBER
                    ),
                )
            )
            continue

        if len(members) != 1:
            raise ValueError(
                "ambiguous universe membership for instrument"
            )

        member = members[0]

        if not member.eligible:
            exclusions.append(
                USPITDateExclusion(
                    market_date=market_date,
                    reason=(
                        USPITExclusionReason
                        .INELIGIBLE_UNIVERSE_MEMBER
                    ),
                )
            )
            continue

        listing = resolve_us_listing_on_date(
            listing_history,
            instrument_id=instrument_id,
            market_date=market_date,
        )

        if (
            listing.canonical_symbol
            != member.canonical_symbol
        ):
            raise ValueError(
                "universe symbol does not match "
                "exact-dated listing identity"
            )

        if listing.listing_mic != member.listing_mic:
            raise ValueError(
                "universe MIC does not match "
                "exact-dated listing identity"
            )

        if (
            listing.security_type
            != member.security_type
        ):
            raise ValueError(
                "universe security_type does not match "
                "exact-dated listing identity"
            )

        if listing.listing_mic != calendar_mic:
            raise ValueError(
                "eligible listing MIC does not match "
                "PIT calendar_mic"
            )

        try:
            bar = resolve_us_daily_bar_on_date(
                daily_history,
                market_date=market_date,
            )
        except ValueError as exc:
            raise ValueError(
                "eligible open session missing exact "
                "US daily bar"
            ) from exc

        if bar.instrument_id != instrument_id:
            raise ValueError(
                "resolved daily bar instrument_id mismatch"
            )

        if bar.calendar_mic != calendar_mic:
            raise ValueError(
                "resolved daily bar calendar_mic mismatch"
            )

        if (
            bar.canonical_symbol
            != listing.canonical_symbol
        ):
            raise ValueError(
                "daily bar symbol does not match "
                "exact-dated listing identity"
            )

        raw_rows.append(bar)
        listing_by_date[market_date] = listing

    raw_rows.sort(
        key=lambda row: row.market_date
    )

    row_dates = tuple(
        row.market_date
        for row in raw_rows
    )

    actions: list[USCorporateActionEvent] = []

    for market_date in _calendar_dates(
        coverage_start,
        coverage_end,
    ):
        actions.extend(
            resolve_us_corporate_actions_on_date(
                action_history,
                market_date=market_date,
            )
        )

    actions.sort(
        key=_action_sort_key
    )

    # State/identity actions are not price transforms, but they still
    # participate in cross-contract coherence checks.
    for action in actions:
        if (
            action.action_type
            == USCorporateActionType.SYMBOL_CHANGE
        ):
            prior_dates = [
                item
                for item in row_dates
                if item < action.effective_date
            ]
            post_dates = [
                item
                for item in row_dates
                if item >= action.effective_date
            ]

            if prior_dates:
                previous = listing_by_date[
                    prior_dates[-1]
                ]

                if (
                    previous.canonical_symbol
                    != action.old_symbol
                ):
                    raise ValueError(
                        "symbol-change old_symbol does not "
                        "match prior eligible identity"
                    )

            if post_dates:
                following = listing_by_date[
                    post_dates[0]
                ]

                if (
                    following.canonical_symbol
                    != action.new_symbol
                ):
                    raise ValueError(
                        "symbol-change new_symbol does not "
                        "match subsequent eligible identity"
                    )

        if (
            action.action_type
            == USCorporateActionType.DELISTING
            and any(
                item > action.effective_date
                for item in row_dates
            )
        ):
            raise ValueError(
                "eligible observation exists after "
                "explicit delisting effective date"
            )

    # Cash dividends deliberately remain unadjusted on this price-basis
    # series. The ex-dividend price move is real market-price behavior.
    # Total-return/dividend-reinvestment semantics, if needed later, require
    # a separate explicit transformation contract.
    price_semantics_unsupported = {
        USCorporateActionType.STOCK_DIVIDEND,
        USCorporateActionType.MERGER,
        USCorporateActionType.SPINOFF,
        USCorporateActionType.RIGHTS,
        USCorporateActionType.OTHER,
    }

    for action in actions:
        if (
            action.action_type
            in price_semantics_unsupported
            and _series_crosses_event(
                row_dates,
                action.effective_date,
            )
        ):
            raise ValueError(
                "unsupported corporate action crosses "
                "assembled price series"
            )

    crossed_splits = [
        action
        for action in actions
        if (
            action.action_type
            == USCorporateActionType.SPLIT
            and _series_crosses_event(
                row_dates,
                action.effective_date,
            )
        )
    ]

    adjusted: list[
        USSplitAdjustedObservation
    ] = []

    try:
        # Explicit precision makes the result independent of ambient
        # Decimal context and matches the existing split-only semantics.
        with localcontext(Context(prec=34)):
            for bar in raw_rows:
                events = [
                    action
                    for action in crossed_splits
                    if (
                        bar.market_date
                        < action.effective_date
                    )
                ]

                events.sort(
                    key=_action_sort_key
                )

                price_factor = Decimal(1)
                volume_factor = Decimal(1)

                for action in events:
                    assert action.new_shares is not None
                    assert action.old_shares is not None

                    price_factor *= (
                        action.old_shares
                        / action.new_shares
                    )
                    volume_factor *= (
                        action.new_shares
                        / action.old_shares
                    )

                if (
                    not price_factor.is_finite()
                    or not volume_factor.is_finite()
                    or price_factor <= 0
                    or volume_factor <= 0
                ):
                    raise ValueError(
                        "unrepresentable split factor"
                    )

                adjusted.append(
                    USSplitAdjustedObservation(
                        instrument_id=bar.instrument_id,
                        market_date=bar.market_date,
                        canonical_symbol=(
                            bar.canonical_symbol
                        ),
                        listing_mic=bar.calendar_mic,
                        open=bar.open * price_factor,
                        high=bar.high * price_factor,
                        low=bar.low * price_factor,
                        close=bar.close * price_factor,
                        volume=(
                            bar.volume
                            * volume_factor
                        ),
                        price_factor=price_factor,
                        volume_factor=volume_factor,
                        split_event_ids=tuple(
                            action.event_id
                            for action in events
                        ),
                    )
                )

    except DecimalException as exc:
        raise ValueError(
            "unrepresentable split factor"
        ) from exc

    exclusions.sort(
        key=lambda item: (
            item.market_date,
            item.reason.value,
        )
    )

    return USRetrospectivePITDailyDataset(
        instrument_id=instrument_id,
        calendar_mic=calendar_mic,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        rows=tuple(raw_rows),
        split_adjusted=tuple(adjusted),
        exclusions=tuple(exclusions),
        transformation=US_PIT_TRANSFORMATION,
        listing_history_id=listing_history.identity,
        session_history_id=session_history.identity,
        daily_history_id=daily_history.identity,
        action_history_id=action_history.identity,
        universe_history_id=universe_history.identity,
        decision_at=decision_at,
        research_built_at=research_built_at,
    )


__all__ = [
    "US_PIT_TRANSFORMATION",
    "USPITExclusionReason",
    "USPITDateExclusion",
    "USSplitAdjustedObservation",
    "USRetrospectivePITDailyDataset",
    "build_us_retrospective_pit_daily_dataset",
]
