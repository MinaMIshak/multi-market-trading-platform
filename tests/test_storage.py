from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.domain import (
    Candidate,
    CandidateSource,
    RiskDecision,
    RiskDecisionType,
    Signal,
    SignalStatus,
    TradePlan,
)
from app.storage import (
    Database,
    SCHEMA_VERSION,
    TradingRepository,
)


CAIRO = ZoneInfo("Africa/Cairo")


def test_database_initializes():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"

        db = Database(path)
        db.initialize()

        assert db.health_check()
        assert (
            db.schema_version()
            == SCHEMA_VERSION
        )


def test_repository_persists_trade_chain():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"

        db = Database(path)
        db.initialize()

        repo = TradingRepository(db)

        now = datetime.now(CAIRO)

        candidate = Candidate(
            symbol="SWDY",
            market_date=now.date(),
            source=CandidateSource.PRE_SURGE,
            probability=Decimal("0.80"),
        )

        repo.save_candidate(candidate)

        signal = Signal(
            candidate_id=candidate.candidate_id,
            symbol="SWDY",
            strategy=CandidateSource.BREAKOUT,
            status=SignalStatus.READY,
            created_at=now,
            expires_at=(
                now
                + timedelta(hours=1)
            ),
            trigger_price=Decimal("10"),
        )

        repo.save_signal(signal)

        plan = TradePlan(
            signal_id=signal.signal_id,
            symbol="SWDY",
            entry_low=Decimal("10"),
            entry_high=Decimal("10"),
            entry_reference=Decimal("10"),
            stop_price=Decimal("9"),
            target_1=Decimal("12"),
            created_at=now,
            valid_until=(
                now
                + timedelta(hours=3)
            ),
        )

        repo.save_trade_plan(plan)

        risk = RiskDecision(
            trade_plan_id=plan.trade_plan_id,
            decision=RiskDecisionType.APPROVE,
            account_equity=Decimal("70000"),
            risk_budget=Decimal("700"),
            approved_risk=Decimal("700"),
            quantity=700,
            max_position_value=Decimal("7000"),
            portfolio_exposure_pct=Decimal("0.10"),
        )

        repo.save_risk_decision(risk)

        assert repo.count_rows(
            "candidates"
        ) == 1

        assert repo.count_rows(
            "signals"
        ) == 1

        assert repo.count_rows(
            "trade_plans"
        ) == 1

        assert repo.count_rows(
            "risk_decisions"
        ) == 1
