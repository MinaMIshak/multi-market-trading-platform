from __future__ import annotations

from datetime import date

from app.core.base_trading_calendar import (
    BaseTradingCalendarPolicy,
)
from app.domain import MarketSession
from app.domain.enums import (
    MarketSessionStatus,
)
from app.storage.market_session_transition_repository import (
    MarketSessionTransitionRepository,
    MarketSessionTransitionResult,
)


class BaseCalendarSessionService:
    """
    Persist deterministic WEEKEND sessions through
    an atomic create-only transition.

    The weekly calendar is lower authority than
    explicit market-session evidence and therefore
    never replaces an existing state.
    """

    def __init__(
        self,
        *,
        trading_repository,
        policy: BaseTradingCalendarPolicy,
        transition_repository: (
            MarketSessionTransitionRepository | None
        ) = None,
    ) -> None:
        self.trading_repository = (
            trading_repository
        )
        self.policy = policy

        if transition_repository is not None:
            self.transition_repository = (
                transition_repository
            )
        else:
            self.transition_repository = (
                MarketSessionTransitionRepository(
                    trading_repository.database
                )
            )

    def apply(
        self,
        market_date: date,
    ) -> MarketSessionStatus:
        status = self.policy.classify(
            market_date
        )

        if status != MarketSessionStatus.WEEKEND:
            return status

        transition = (
            self.transition_repository
            .compare_and_promote(
                MarketSession(
                    market_date=market_date,
                    status=MarketSessionStatus.WEEKEND,
                ),
                replaceable_statuses=frozenset(),
            )
        )

        if transition in (
            MarketSessionTransitionResult.CREATED,
            MarketSessionTransitionResult.UNCHANGED,
        ):
            return MarketSessionStatus.WEEKEND

        if (
            transition
            == MarketSessionTransitionResult.CONFLICT
        ):
            existing = (
                self.trading_repository
                .get_market_session(
                    market_date
                )
            )

            if existing is None:
                return MarketSessionStatus.UNKNOWN

            return existing.status

        # REPLACED is impossible for a create-only
        # transition. Fail closed if the repository
        # contract is ever violated.
        return MarketSessionStatus.UNKNOWN
