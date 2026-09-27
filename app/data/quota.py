"""Automatic quota admission. Costs are verified by the caller, never inferred.

A cost contract covers one complete invocation, including any internal requests.
Transports used here must not retry internally. The boundary performs exactly one
attempt; later scheduler/fallback attempts reserve again. Failed/crashed attempts
are never refunded. All processes for a provider account must share this database.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
import sqlite3
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from app.storage import Database


class QuotaRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class QuotaPolicy:
    daily_allowance: int = 20
    automatic_budget: int = 15
    protected_reserve: int = 5
    extra_credits: int = 0
    consume_extra_credits: bool = False

    def capacity(self) -> int:
        values = (self.daily_allowance, self.automatic_budget,
                  self.protected_reserve, self.extra_credits)
        if any(type(v) is not int or v < 0 for v in values):
            raise QuotaRejected("invalid quota configuration")
        if (not 1 <= self.daily_allowance <= 20
                or not 1 <= self.automatic_budget <= 15
                or self.protected_reserve < 5
                or self.protected_reserve >= self.daily_allowance
                or self.consume_extra_credits is not False):
            raise QuotaRejected("unsafe quota configuration")
        return min(self.automatic_budget,
                   self.daily_allowance - self.protected_reserve)


@dataclass(frozen=True)
class VerifiedQuotaCost:
    """Audited upper bound for the specified operation and its request inputs.

    evidence identifies the verified contract; it must not contain credentials.
    No production provider contract is supplied by this milestone.
    """
    units: int
    evidence: str


class QuotaGuard:
    def __init__(self, database: Database, *, policy: QuotaPolicy = QuotaPolicy(),
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.database = database
        self.policy = policy
        self.clock = clock

    def admit(self, cost: VerifiedQuotaCost | None) -> None:
        # One shared account budget, never partitioned by symbol/operation/token.
        if (type(cost) is not VerifiedQuotaCost
                or type(cost.units) is not int or cost.units <= 0
                or type(cost.evidence) is not str or not cost.evidence.strip()):
            raise QuotaRejected("verified quota cost required")
        if type(self.policy) is not QuotaPolicy:
            raise QuotaRejected("invalid quota configuration")
        capacity = self.policy.capacity()
        try:
            with closing(self.database.connect()) as con, con:
                con.execute("PRAGMA synchronous = FULL")
                con.execute("BEGIN IMMEDIATE")
                now = self.clock()
                if not isinstance(now, datetime) or now.utcoffset() is None:
                    raise QuotaRejected("aware quota clock required")
                day = now.astimezone(timezone.utc).date().isoformat()
                row = con.execute(
                    "SELECT quota_day, used_units FROM automatic_quota WHERE id = 1"
                ).fetchone()
                if row is None:
                    raise QuotaRejected("quota ledger unavailable")
                previous, used = row
                try:
                    if date.fromisoformat(previous).isoformat() != previous:
                        raise ValueError
                except (TypeError, ValueError):
                    raise QuotaRejected("invalid quota ledger") from None
                if previous > day:
                    raise QuotaRejected("quota clock moved backwards")
                if type(used) is not int or used < 0:
                    raise QuotaRejected("invalid quota ledger")
                if previous < day:
                    used = 0
                if cost.units > capacity - used:
                    raise QuotaRejected("automatic quota budget exhausted")
                con.execute(
                    "UPDATE automatic_quota SET quota_day = ?, used_units = ? WHERE id = 1",
                    (day, used + cost.units),
                )
            # Commit completes before transport; no refund on any later failure.
        except (sqlite3.Error, OSError):
            raise QuotaRejected("quota ledger unavailable") from None


class QuotaLimitedDailyProvider:
    """Only the currently supported automatic operation is exposed.

    cost_contract is local, deterministic, and must perform no network I/O.
    It receives the exact request inputs and returns a verified upper bound.
    """
    def __init__(self, provider, guard: QuotaGuard, cost_contract=None):
        self._provider = provider
        self._guard = guard
        self._cost_contract = cost_contract

    @property
    def name(self):
        return self._provider.name

    def fetch_daily_bars(self, *, symbol, start_date, end_date):
        provider_name = self.name
        inputs = dict(symbol=symbol, start_date=start_date, end_date=end_date)
        try:
            cost = (self._cost_contract(**inputs)
                    if self._cost_contract is not None else None)
        except Exception:
            raise QuotaRejected("quota cost contract unavailable") from None
        if self.name != provider_name:
            raise QuotaRejected("provider identity changed during quota admission")
        self._guard.admit(cost)
        # Admission callbacks must not switch the alias-bound source before
        # transport. A committed reservation is never refunded on rejection.
        if self.name != provider_name:
            raise QuotaRejected("provider identity changed during quota admission")
        try:
            return self._provider.fetch_daily_bars(**inputs)
        except Exception:
            raise RuntimeError("automatic provider attempt failed") from None
