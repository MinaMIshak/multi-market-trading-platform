from datetime import date, datetime, timezone

from app.core.calendar_truth import (
    CalendarTruthResolver,
)
from app.core.holiday_promotion import (
    HolidayPromotionAction,
    HolidayPromotionPolicy,
)
from app.core.holiday_promotion_service import (
    HolidayPromotionService,
)
from app.core.holiday_verification import (
    HolidayVerificationPolicy,
)
from app.core.holiday_verification_service import (
    HolidayVerificationService,
)
from app.core.schedule import CalendarTruth
from app.domain import MarketSession
from app.domain.enums import (
    MarketSessionStatus,
)
from app.storage import (
    Database,
    TradingRepository,
)
from app.storage.holiday_evidence_repository import (
    HolidayEvidenceRepository,
)
from app.storage.market_session_transition_repository import (
    MarketSessionTransitionRepository,
    MarketSessionTransitionResult,
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

    trading = TradingRepository(database)

    evidence = HolidayEvidenceRepository(
        database
    )

    verification = HolidayVerificationService(
        repository=evidence,
        policy=HolidayVerificationPolicy(),
    )

    transitions = (
        MarketSessionTransitionRepository(
            database
        )
    )

    promotion = HolidayPromotionService(
        verification_service=verification,
        trading_repository=trading,
        promotion_policy=(
            HolidayPromotionPolicy()
        ),
        transition_repository=transitions,
    )

    resolver = CalendarTruthResolver(
        trading
    )

    return (
        trading,
        evidence,
        promotion,
        resolver,
    )


def save_evidence(
    repository,
    *,
    authority="egx_official",
    closed=True,
    content_hash="a" * 64,
):
    repository.save_evidence(
        authority=authority,
        observed_date=DAY,
        market_closed=closed,
        source_uri=(
            "https://example.test/"
            + content_hash[0]
        ),
        content_hash=content_hash,
        received_at=NOW,
    )


def test_verified_egx_closure_promotes_end_to_end(
    tmp_path,
):
    (
        trading,
        evidence,
        promotion,
        resolver,
    ) = build(tmp_path)

    save_evidence(evidence)

    outcome = promotion.promote(DAY)

    assert (
        outcome.promotion.action
        == HolidayPromotionAction.CREATE
    )

    assert (
        outcome.transition
        == MarketSessionTransitionResult.CREATED
    )

    session = trading.get_market_session(
        DAY
    )

    assert session is not None
    assert (
        session.status
        == MarketSessionStatus.HOLIDAY
    )

    assert (
        resolver.resolve(DAY)
        == CalendarTruth
        .VERIFIED_NON_TRADING_DAY
    )


def test_government_only_never_promotes(
    tmp_path,
):
    (
        trading,
        evidence,
        promotion,
        resolver,
    ) = build(tmp_path)

    save_evidence(
        evidence,
        authority="egypt_government",
    )

    outcome = promotion.promote(DAY)

    assert (
        outcome.promotion.action
        == HolidayPromotionAction.NOOP
    )
    assert outcome.transition is None

    assert (
        trading.get_market_session(DAY)
        is None
    )

    assert (
        resolver.resolve(DAY)
        == CalendarTruth.UNVERIFIED
    )


def test_weekend_is_superseded_by_official_holiday(
    tmp_path,
):
    (
        trading,
        evidence,
        promotion,
        resolver,
    ) = build(tmp_path)

    trading.save_market_session(
        MarketSession(
            market_date=DAY,
            status=MarketSessionStatus.WEEKEND,
        )
    )

    save_evidence(evidence)

    outcome = promotion.promote(DAY)

    assert (
        outcome.promotion.action
        == HolidayPromotionAction.REPLACE
    )

    assert (
        outcome.transition
        == MarketSessionTransitionResult.REPLACED
    )

    assert (
        trading.get_market_session(DAY).status
        == MarketSessionStatus.HOLIDAY
    )

    assert (
        resolver.resolve(DAY)
        == CalendarTruth
        .VERIFIED_NON_TRADING_DAY
    )


def test_existing_verified_session_is_preserved(
    tmp_path,
):
    (
        trading,
        evidence,
        promotion,
        resolver,
    ) = build(tmp_path)

    trading.save_market_session(
        MarketSession(
            market_date=DAY,
            status=MarketSessionStatus.VERIFIED,
            data_verified_at=NOW,
        )
    )

    save_evidence(evidence)

    outcome = promotion.promote(DAY)

    assert (
        outcome.promotion.action
        == HolidayPromotionAction.CONFLICT
    )
    assert outcome.transition is None

    session = trading.get_market_session(
        DAY
    )

    assert (
        session.status
        == MarketSessionStatus.VERIFIED
    )

    assert (
        resolver.resolve(DAY)
        == CalendarTruth
        .VERIFIED_TRADING_DAY
    )


def test_conflicting_egx_evidence_writes_nothing(
    tmp_path,
):
    (
        trading,
        evidence,
        promotion,
        resolver,
    ) = build(tmp_path)

    save_evidence(
        evidence,
        closed=True,
        content_hash="a" * 64,
    )

    save_evidence(
        evidence,
        closed=False,
        content_hash="b" * 64,
    )

    outcome = promotion.promote(DAY)

    assert (
        outcome.verification.status
        == MarketSessionStatus.UNKNOWN
    )

    assert (
        outcome.promotion.action
        == HolidayPromotionAction.NOOP
    )

    assert outcome.transition is None

    assert (
        trading.get_market_session(DAY)
        is None
    )

    assert (
        resolver.resolve(DAY)
        == CalendarTruth.UNVERIFIED
    )
