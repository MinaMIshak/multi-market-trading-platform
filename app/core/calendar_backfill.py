from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from app.core.base_calendar_service import (
    BaseCalendarSessionService,
)
from app.core.base_trading_calendar import (
    BaseTradingCalendarPolicy,
)
from app.core.calendar_evidence import (
    OfficialIndexEvidenceRepository,
)
from app.core.calendar_verification import (
    CalendarVerificationPolicy,
)
from app.core.calendar_verification_service import (
    CalendarVerificationService,
)
from app.core.historical_calendar_verification import (
    HistoricalCalendarVerificationPolicy,
    HistoricalOfficialIndexEvidenceRepository,
)
from app.core.holiday_promotion import HolidayPromotionPolicy
from app.core.holiday_promotion_service import (
    HolidayPromotionService,
)
from app.core.holiday_verification import (
    HolidayVerificationPolicy,
)
from app.core.holiday_verification_service import (
    HolidayVerificationService,
)
from app.data.validated_index_repository import (
    ValidatedCanonicalIndexRepository,
)
from app.domain.enums import MarketSessionStatus
from app.storage.database import Database
from app.storage.holiday_evidence_repository import (
    HolidayEvidenceRepository,
)
from app.storage.market_session_transition_repository import (
    MarketSessionTransitionRepository,
)
from app.storage.repository import TradingRepository


@dataclass(frozen=True)
class CalendarBackfillDateResult:
    market_date: date
    base_status: MarketSessionStatus
    holiday_status: MarketSessionStatus
    verification_status: MarketSessionStatus
    verification_basis: str | None = None


@dataclass(frozen=True)
class CalendarBackfillRuntime:
    """
    Offline/manual recovery of calendar-session truth from evidence
    already admitted to this database.

    Reuses the same components the scheduler dispatches
    (BaseCalendarSessionService, HolidayPromotionService,
    CalendarVerificationService) without the scheduler job ledger, so a
    date range already covered by already-persisted official-index or
    holiday evidence can be resolved in one pass. Performs no network
    acquisition: WEEKEND is deterministic weekday classification;
    HOLIDAY and VERIFIED require evidence already present in the
    database and fail closed to UNKNOWN otherwise.

    When a historical verification service is configured, a date the
    same-day (live) policy leaves unverified may still be VERIFIED from
    admitted official index bars snapshotted after that date
    (verification_basis=HISTORICAL_OFFICIAL). The same compare-and-
    promote rules apply: HOLIDAY conflicts fail closed, while the
    deterministic WEEKEND default is superseded by official evidence
    exactly as on the live path (TradingDayPromotionPolicy).
    """

    base_service: BaseCalendarSessionService
    holiday_promotion_service: HolidayPromotionService
    verification_service: CalendarVerificationService
    historical_verification_service: (
        CalendarVerificationService | None
    ) = None

    def run_date(
        self,
        market_date: date,
    ) -> CalendarBackfillDateResult:
        base_status = self.base_service.apply(market_date)

        holiday_outcome = self.holiday_promotion_service.promote(
            market_date
        )

        verification_decision = self.verification_service.verify(
            market_date
        )
        verification_basis = (
            "SAME_DAY_OFFICIAL"
            if verification_decision.status
            == MarketSessionStatus.VERIFIED
            else None
        )

        if (
            verification_basis is None
            and self.historical_verification_service is not None
        ):
            verification_decision = (
                self.historical_verification_service.verify(
                    market_date
                )
            )
            if (
                verification_decision.status
                == MarketSessionStatus.VERIFIED
            ):
                verification_basis = "HISTORICAL_OFFICIAL"

        return CalendarBackfillDateResult(
            market_date=market_date,
            base_status=base_status,
            holiday_status=holiday_outcome.verification.status,
            verification_status=verification_decision.status,
            verification_basis=verification_basis,
        )

    def run_range(
        self,
        start_date: date,
        end_date: date,
    ) -> tuple[CalendarBackfillDateResult, ...]:
        if end_date < start_date:
            raise ValueError(
                "end_date must not precede start_date"
            )

        results = []
        current = start_date

        while current <= end_date:
            results.append(self.run_date(current))
            current += timedelta(days=1)

        return tuple(results)


def build_calendar_backfill_runtime(
    *,
    database: Database,
    data_root: str | Path | None = None,
) -> CalendarBackfillRuntime:
    trading_repository = TradingRepository(database)
    transition_repository = MarketSessionTransitionRepository(
        database
    )
    holiday_evidence_repository = HolidayEvidenceRepository(
        database
    )
    official_index_evidence_repository = (
        OfficialIndexEvidenceRepository(database)
    )

    base_service = BaseCalendarSessionService(
        trading_repository=trading_repository,
        policy=BaseTradingCalendarPolicy(),
        transition_repository=transition_repository,
    )

    holiday_verification_service = HolidayVerificationService(
        repository=holiday_evidence_repository,
        policy=HolidayVerificationPolicy(),
    )

    holiday_promotion_service = HolidayPromotionService(
        verification_service=holiday_verification_service,
        trading_repository=trading_repository,
        promotion_policy=HolidayPromotionPolicy(),
        transition_repository=transition_repository,
    )

    verification_service = CalendarVerificationService(
        evidence_repository=official_index_evidence_repository,
        trading_repository=trading_repository,
        policy=CalendarVerificationPolicy(),
        transition_repository=transition_repository,
    )

    historical_verification_service = None

    if data_root is not None:
        historical_verification_service = CalendarVerificationService(
            evidence_repository=(
                HistoricalOfficialIndexEvidenceRepository(
                    validated_reader=(
                        ValidatedCanonicalIndexRepository
                        .from_data_root(
                            database=database,
                            data_root=data_root,
                        )
                    ),
                )
            ),
            trading_repository=trading_repository,
            policy=HistoricalCalendarVerificationPolicy(),
            transition_repository=transition_repository,
        )

    return CalendarBackfillRuntime(
        base_service=base_service,
        holiday_promotion_service=holiday_promotion_service,
        verification_service=verification_service,
        historical_verification_service=(
            historical_verification_service
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """
    Reproducible CLI for the offline calendar-session recovery pass.

    Explicit --start-date/--end-date only: this is a manual recovery
    tool, not a scheduled job, so it never infers a range. Performs no
    network acquisition; WEEKEND/HOLIDAY/VERIFIED are only ever derived
    from evidence already admitted to the target database.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Recover market-session calendar truth for a date range "
            "from evidence already admitted to the database "
            "(deterministic WEEKEND; HOLIDAY/VERIFIED fail closed to "
            "UNKNOWN absent admitted evidence)."
        )
    )
    parser.add_argument(
        "--start-date",
        required=True,
        type=date.fromisoformat,
    )
    parser.add_argument(
        "--end-date",
        required=True,
        type=date.fromisoformat,
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=(
            "Defaults to the EGX_DB_PATH environment variable, "
            "then /app/data/platform.db."
        ),
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help=(
            "Platform data root holding the admitted official index "
            "store used for historical session verification. Defaults "
            "to the --db-path parent directory, matching "
            "scheduler_worker."
        ),
    )
    args = parser.parse_args(argv)

    db_path = args.db_path or os.getenv(
        "EGX_DB_PATH",
        "/app/data/platform.db",
    )

    database = Database(db_path)
    database.initialize()

    data_root = (
        Path(args.data_root)
        if args.data_root
        else Path(db_path).parent
    )

    runtime = build_calendar_backfill_runtime(
        database=database,
        data_root=data_root,
    )
    results = runtime.run_range(args.start_date, args.end_date)

    for result in results:
        print(
            f"{result.market_date} "
            f"base={result.base_status.value} "
            f"holiday={result.holiday_status.value} "
            f"verification={result.verification_status.value}"
            f" basis={result.verification_basis or 'NONE'}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
