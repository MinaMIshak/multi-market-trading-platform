from __future__ import annotations

import json

from datetime import date, datetime, timezone

from app.core.calendar_backfill import (
    build_calendar_backfill_runtime,
    main,
)
from app.core.historical_calendar_verification import (
    HistoricalCalendarVerificationPolicy,
    HistoricalOfficialIndexEvidence,
    HistoricalOfficialIndexEvidenceRepository,
)
from app.data.official_index_refresh_job import (
    OFFICIAL_PROVIDER,
    REQUIRED_OFFICIAL_INDICES,
)
from app.data.official_index_refresh_runtime import (
    build_official_index_refresh_runtime,
)
from app.data.provider import (
    ProviderBatchResponse,
    ProviderResponse,
)
from app.data.validated_index_repository import (
    ValidatedCanonicalIndexRepository,
)
from app.domain import MarketSession
from app.domain.enums import MarketSessionStatus
from app.storage import Database
from app.storage.repository import TradingRepository


SUNDAY = date(2026, 9, 20)
MONDAY = date(2026, 9, 21)
TUESDAY = date(2026, 9, 22)
WEDNESDAY = date(2026, 9, 23)
FRIDAY = date(2026, 9, 25)
SNAPSHOT = date(2026, 9, 29)


def index_row(market_date: date):
    return {
        "change": 5,
        "changePer": 5,
        "high": 110,
        "indexClose": 105,
        "indexDay": market_date.isoformat() + "T00:00:00",
        "indexOpen": 100,
        "low": 90,
    }


class FakeOfficialProvider:
    name = OFFICIAL_PROVIDER

    def __init__(self, bar_dates, *, skip=None) -> None:
        self.bar_dates = tuple(bar_dates)
        self.skip = skip or {}

    def fetch_index_bars(
        self,
        *,
        index_name,
        start_date,
        end_date,
        page_size=1000,
    ):
        dates = [
            day
            for day in self.bar_dates
            if day not in self.skip.get(index_name, ())
        ]
        document = {
            "success": True,
            "data": [index_row(day) for day in reversed(dates)],
            "page": 1,
            "pageSize": page_size,
            "totalCount": len(dates),
            "totalPages": 1,
            "hasNextPage": False,
            "hasPreviousPage": False,
        }
        payload = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        response = ProviderResponse(
            payload=payload,
            filename=(
                f"{index_name}-{start_date.isoformat()}-"
                f"{end_date.isoformat()}-page-0001.json"
            ),
            source_uri="https://example.test/official-index",
            record_count=len(dates),
            metadata={"page_number": 1},
        )
        return ProviderBatchResponse(
            responses=(response,),
            record_count=len(dates),
            metadata={
                "provider": self.name,
                "index_name": index_name,
                "calculated_page_count": 1,
            },
        )


def admit(tmp_path, provider, *, start, end, snapshot):
    database = Database(tmp_path / "platform.db")
    database.initialize()
    runtime = build_official_index_refresh_runtime(
        database=database,
        root=tmp_path,
    )
    runtime.job.run(
        provider=provider,
        start_date=start,
        end_date=end,
        snapshot_date=snapshot,
    )
    return database


def repository(tmp_path, database):
    return HistoricalOfficialIndexEvidenceRepository(
        validated_reader=ValidatedCanonicalIndexRepository.from_data_root(
            database=database,
            data_root=tmp_path,
        ),
    )


def test_bar_in_later_validated_snapshot_verifies_session(tmp_path):
    database = admit(
        tmp_path,
        FakeOfficialProvider([MONDAY, TUESDAY]),
        start=SUNDAY, end=TUESDAY, snapshot=SNAPSHOT,
    )

    evidence = repository(tmp_path, database).load(MONDAY)

    assert {item.index_name for item in evidence} == set(
        REQUIRED_OFFICIAL_INDICES
    )
    assert all(item.bar_present for item in evidence)
    assert all(
        item.source_snapshot_date == SNAPSHOT for item in evidence
    )

    decision = HistoricalCalendarVerificationPolicy().evaluate(
        market_date=MONDAY,
        evidence=evidence,
    )
    assert decision.status == MarketSessionStatus.VERIFIED
    assert decision.reasons == (
        "historical_official_index_bars_confirm_target_date",
    )


def test_date_inside_range_without_bar_stays_unknown_not_holiday(
    tmp_path,
):
    database = admit(
        tmp_path,
        FakeOfficialProvider([MONDAY, WEDNESDAY]),
        start=MONDAY, end=WEDNESDAY, snapshot=SNAPSHOT,
    )

    evidence = repository(tmp_path, database).load(TUESDAY)
    decision = HistoricalCalendarVerificationPolicy().evaluate(
        market_date=TUESDAY,
        evidence=evidence,
    )

    assert len(evidence) == len(REQUIRED_OFFICIAL_INDICES)
    assert not any(item.bar_present for item in evidence)
    assert decision.status == MarketSessionStatus.UNKNOWN
    assert decision.reasons == tuple(
        f"{name}:target_bar_absent"
        for name in sorted(REQUIRED_OFFICIAL_INDICES)
    )


def test_date_before_first_admitted_bar_has_no_evidence(tmp_path):
    database = admit(
        tmp_path,
        FakeOfficialProvider([MONDAY, TUESDAY]),
        start=SUNDAY, end=TUESDAY, snapshot=SNAPSHOT,
    )

    assert repository(tmp_path, database).load(SUNDAY) == []


def test_one_required_index_missing_bar_fails_closed(tmp_path):
    database = admit(
        tmp_path,
        FakeOfficialProvider(
            [MONDAY, TUESDAY, WEDNESDAY],
            skip={"EGX70_EWI": (TUESDAY,)},
        ),
        start=MONDAY, end=WEDNESDAY, snapshot=SNAPSHOT,
    )

    decision = HistoricalCalendarVerificationPolicy().evaluate(
        market_date=TUESDAY,
        evidence=repository(tmp_path, database).load(TUESDAY),
    )

    assert decision.status == MarketSessionStatus.UNKNOWN
    assert decision.reasons == ("EGX70_EWI:target_bar_absent",)


def test_same_day_snapshot_is_not_historical_evidence(tmp_path):
    database = admit(
        tmp_path,
        FakeOfficialProvider([MONDAY]),
        start=MONDAY, end=MONDAY, snapshot=MONDAY,
    )

    evidence = repository(tmp_path, database).load(MONDAY)
    decision = HistoricalCalendarVerificationPolicy().evaluate(
        market_date=MONDAY,
        evidence=evidence,
    )

    assert evidence == []
    assert decision.status == MarketSessionStatus.UNKNOWN
    assert "CASE30:evidence_count=0" in decision.reasons


def test_date_outside_artifact_range_has_no_evidence(tmp_path):
    database = admit(
        tmp_path,
        FakeOfficialProvider([MONDAY]),
        start=MONDAY, end=MONDAY, snapshot=SNAPSHOT,
    )

    assert repository(tmp_path, database).load(TUESDAY) == []


def test_tampered_canonical_file_fails_closed(tmp_path):
    database = admit(
        tmp_path,
        FakeOfficialProvider([MONDAY]),
        start=MONDAY, end=MONDAY, snapshot=SNAPSHOT,
    )

    for path in (tmp_path / "canonical").rglob("CASE30-*.json"):
        path.write_bytes(path.read_bytes() + b" ")

    evidence = repository(tmp_path, database).load(MONDAY)
    decision = HistoricalCalendarVerificationPolicy().evaluate(
        market_date=MONDAY,
        evidence=evidence,
    )

    assert "CASE30" not in {item.index_name for item in evidence}
    assert decision.status == MarketSessionStatus.UNKNOWN
    assert "CASE30:evidence_count=0" in decision.reasons


def test_policy_rejects_non_official_provider_and_same_day_snapshot():
    evidence = [
        HistoricalOfficialIndexEvidence(
            provider="other",
            index_name="CASE30",
            artifact_id="a",
            source_snapshot_date=SNAPSHOT,
            bar_present=True,
        ),
        HistoricalOfficialIndexEvidence(
            provider=OFFICIAL_PROVIDER,
            index_name="EGX70_EWI",
            artifact_id="b",
            source_snapshot_date=MONDAY,
            bar_present=True,
        ),
        HistoricalOfficialIndexEvidence(
            provider=OFFICIAL_PROVIDER,
            index_name="EGX100_EWI",
            artifact_id="c",
            source_snapshot_date=SNAPSHOT,
            bar_present=True,
        ),
    ]

    decision = HistoricalCalendarVerificationPolicy().evaluate(
        market_date=MONDAY,
        evidence=evidence,
    )

    assert decision.status == MarketSessionStatus.UNKNOWN
    assert "CASE30:provider" in decision.reasons
    assert "EGX70_EWI:snapshot_not_after_target" in decision.reasons


def test_backfill_uses_historical_evidence_only_as_fallback(tmp_path):
    database = admit(
        tmp_path,
        FakeOfficialProvider([MONDAY, TUESDAY]),
        start=SUNDAY, end=TUESDAY, snapshot=SNAPSHOT,
    )

    runtime = build_calendar_backfill_runtime(
        database=database,
        data_root=tmp_path,
    )
    results = {
        r.market_date: r for r in runtime.run_range(SUNDAY, TUESDAY)
    }

    assert results[MONDAY].verification_status == (
        MarketSessionStatus.VERIFIED
    )
    assert results[MONDAY].verification_basis == "HISTORICAL_OFFICIAL"
    assert results[TUESDAY].verification_status == (
        MarketSessionStatus.VERIFIED
    )
    assert results[SUNDAY].verification_status == (
        MarketSessionStatus.UNKNOWN
    )
    assert results[SUNDAY].verification_basis is None

    trading = TradingRepository(database)
    assert trading.get_market_session(MONDAY).status == (
        MarketSessionStatus.VERIFIED
    )
    assert trading.get_market_session(SUNDAY) is None or (
        trading.get_market_session(SUNDAY).status
        != MarketSessionStatus.VERIFIED
    )


def test_backfill_without_canonical_root_keeps_live_policy_only(
    tmp_path,
):
    database = admit(
        tmp_path,
        FakeOfficialProvider([MONDAY]),
        start=MONDAY, end=MONDAY, snapshot=SNAPSHOT,
    )

    runtime = build_calendar_backfill_runtime(database=database)
    (result,) = runtime.run_range(MONDAY, MONDAY)

    assert result.verification_status == MarketSessionStatus.UNKNOWN
    assert result.verification_basis is None


def test_historical_bar_conflicting_with_holiday_fails_closed(tmp_path):
    database = admit(
        tmp_path,
        FakeOfficialProvider([MONDAY]),
        start=MONDAY, end=MONDAY, snapshot=SNAPSHOT,
    )
    TradingRepository(database).save_market_session(
        MarketSession(
            market_date=MONDAY,
            status=MarketSessionStatus.HOLIDAY,
        )
    )

    runtime = build_calendar_backfill_runtime(
        database=database,
        data_root=tmp_path,
    )
    (result,) = runtime.run_range(MONDAY, MONDAY)

    assert result.verification_status == MarketSessionStatus.UNKNOWN
    assert result.verification_basis is None
    assert TradingRepository(database).get_market_session(
        MONDAY
    ).status == MarketSessionStatus.HOLIDAY


def test_historical_bar_on_weekend_follows_existing_promotion_policy(
    tmp_path,
):
    # TradingDayPromotionPolicy treats WEEKEND as a lower-authority
    # deterministic default that official trading evidence supersedes
    # (same rule as the live same-day path).
    database = admit(
        tmp_path,
        FakeOfficialProvider([FRIDAY]),
        start=FRIDAY, end=FRIDAY, snapshot=SNAPSHOT,
    )

    runtime = build_calendar_backfill_runtime(
        database=database,
        data_root=tmp_path,
    )
    (result,) = runtime.run_range(FRIDAY, FRIDAY)

    assert result.base_status == MarketSessionStatus.WEEKEND
    assert result.verification_status == MarketSessionStatus.VERIFIED
    assert result.verification_basis == "HISTORICAL_OFFICIAL"


def test_cli_default_data_root_enables_historical_verification(
    tmp_path, capsys,
):
    admit(
        tmp_path,
        FakeOfficialProvider([MONDAY]),
        start=MONDAY, end=MONDAY, snapshot=SNAPSHOT,
    )

    exit_code = main([
        "--db-path", str(tmp_path / "platform.db"),
        "--start-date", MONDAY.isoformat(),
        "--end-date", MONDAY.isoformat(),
    ])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "verification=VERIFIED" in output
    assert "basis=HISTORICAL_OFFICIAL" in output

    session = TradingRepository(
        Database(tmp_path / "platform.db")
    ).get_market_session(MONDAY)
    assert session.status == MarketSessionStatus.VERIFIED
    assert session.data_verified_at is not None
    # Recording time is when the pass ran, never backdated to the
    # market date.
    assert session.data_verified_at > datetime(
        2026, 9, 22, tzinfo=timezone.utc
    )
