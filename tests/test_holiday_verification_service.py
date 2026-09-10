from datetime import (
    date,
    datetime,
    timezone,
)

from app.core.holiday_verification import (
    HolidayVerificationPolicy,
)
from app.core.holiday_verification_service import (
    HolidayVerificationService,
)
from app.domain.enums import (
    MarketSessionStatus,
)
from app.storage import Database
from app.storage.holiday_evidence_repository import (
    HolidayEvidenceRepository,
)


DAY = date(2026, 7, 2)
NOW = datetime(
    2026,
    7,
    1,
    10,
    0,
    tzinfo=timezone.utc,
)


def build(tmp_path):
    database = Database(
        tmp_path / "platform.db"
    )
    database.initialize()

    repository = HolidayEvidenceRepository(
        database
    )

    service = HolidayVerificationService(
        repository=repository,
        policy=HolidayVerificationPolicy(),
    )

    return repository, service


def save(
    repository,
    *,
    authority,
    closed=True,
    uri="https://example.test/notice",
    content_hash="a" * 64,
):
    repository.save_evidence(
        authority=authority,
        observed_date=DAY,
        market_closed=closed,
        source_uri=uri,
        content_hash=content_hash,
        received_at=NOW,
    )


def test_persisted_egx_closure_verifies_holiday(
    tmp_path,
):
    repository, service = build(tmp_path)

    save(
        repository,
        authority="egx_official",
    )

    decision = service.evaluate(DAY)

    assert (
        decision.status
        == MarketSessionStatus.HOLIDAY
    )


def test_government_only_remains_unknown(
    tmp_path,
):
    repository, service = build(tmp_path)

    save(
        repository,
        authority="egypt_government",
    )

    decision = service.evaluate(DAY)

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )

    assert decision.reasons == (
        "awaiting_egx_closure_confirmation",
    )


def test_no_evidence_remains_unknown(
    tmp_path,
):
    _, service = build(tmp_path)

    decision = service.evaluate(DAY)

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )

    assert decision.reasons == (
        "no_target_date_holiday_evidence",
    )


def test_unknown_authority_fails_closed(
    tmp_path,
):
    repository, service = build(tmp_path)

    save(
        repository,
        authority="unknown_authority",
    )

    decision = service.evaluate(DAY)

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )

    assert decision.reasons == (
        "unsupported_holiday_authority",
    )


def test_conflicting_egx_sources_fail_closed(
    tmp_path,
):
    repository, service = build(tmp_path)

    save(
        repository,
        authority="egx_official",
        closed=True,
        uri="https://example.test/closure",
        content_hash="a" * 64,
    )

    save(
        repository,
        authority="egx_official",
        closed=False,
        uri="https://example.test/open",
        content_hash="b" * 64,
    )

    decision = service.evaluate(DAY)

    assert (
        decision.status
        == MarketSessionStatus.UNKNOWN
    )

    assert decision.reasons == (
        "conflicting_egx_closure_evidence",
    )
