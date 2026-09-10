from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from dataclasses import replace
import socket

import pytest

from app.data.quota import (QuotaGuard, QuotaPolicy, QuotaRejected,
                            VerifiedQuotaCost, QuotaLimitedDailyProvider)
from app.core.daily_refresh_runtime import build_daily_refresh_runtime
from app.storage import Database
from app.storage.scheduler_repository import SchedulerRepository


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("real network forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "quota.db")
    database.initialize()
    return database


def cost(units):
    return VerifiedQuotaCost(units, "offline verified fixture")


def usage(db):
    with db.connect() as con:
        return con.execute("SELECT used_units FROM automatic_quota").fetchone()[0]


class Fake:
    name = "fake"
    calls = 0
    fail = False

    def fetch_daily_bars(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise ValueError("sensitive provider detail")
        return kwargs


def fetch(provider):
    return provider.fetch_daily_bars(symbol="TEST", start_date=date(2026, 1, 1),
                                     end_date=date(2026, 1, 2))


def test_costs_accumulate_exact_boundary_and_reserve(db):
    guard = QuotaGuard(db)
    for units in (2, 3, 10):
        guard.admit(cost(units))
    assert usage(db) == 15
    with pytest.raises(QuotaRejected):
        guard.admit(cost(1))
    assert usage(db) == 15


@pytest.mark.parametrize("invalid", [None, 1, "unknown", cost(None), cost(0), cost(-1),
                                     cost(True), cost(1.5), cost(float('nan')),
                                     VerifiedQuotaCost(1, "")])
def test_invalid_cost_no_provider_calls(db, invalid):
    fake = Fake()
    wrapped = QuotaLimitedDailyProvider(fake, QuotaGuard(db), lambda **kw: invalid)
    with pytest.raises(QuotaRejected):
        fetch(wrapped)
    assert fake.calls == usage(db) == 0


@pytest.mark.parametrize("changes", [dict(daily_allowance=None), dict(daily_allowance=0),
    dict(daily_allowance=21), dict(automatic_budget=16), dict(automatic_budget=0),
    dict(automatic_budget=True), dict(protected_reserve=4), dict(protected_reserve=20),
    dict(extra_credits=-1), dict(consume_extra_credits=True), dict(consume_extra_credits=None)])
def test_invalid_policy(db, changes):
    guard = QuotaGuard(db, policy=replace(QuotaPolicy(), **changes))
    with pytest.raises(QuotaRejected):
        guard.admit(cost(1))
    assert usage(db) == 0


def test_extra_credits_and_lower_configured_budget(db):
    guard = QuotaGuard(db, policy=QuotaPolicy(automatic_budget=12, extra_credits=1000))
    guard.admit(cost(12))
    with pytest.raises(QuotaRejected):
        guard.admit(cost(1))
    assert usage(db) == 12


def test_restart(db):
    QuotaGuard(db).admit(cost(14))
    restarted = Database(db.path)
    restarted.initialize()
    QuotaGuard(restarted).admit(cost(1))
    with pytest.raises(QuotaRejected):
        QuotaGuard(restarted).admit(cost(1))
    assert usage(db) == 15


def test_utc_reset_and_clock_rollback(db):
    now = [datetime.fromisoformat("2026-09-10T23:59:59+00:00")]
    guard = QuotaGuard(db, clock=lambda: now[0])
    guard.admit(cost(15))
    now[0] = datetime.fromisoformat("2026-09-11T02:59:59+03:00")
    with pytest.raises(QuotaRejected):
        guard.admit(cost(1))
    now[0] = datetime.fromisoformat("2026-09-11T00:00:00+00:00")
    guard.admit(cost(2))
    assert usage(db) == 2
    now[0] = datetime.fromisoformat("2026-09-10T23:59:59+00:00")
    with pytest.raises(QuotaRejected):
        guard.admit(cost(1))


def test_naive_clock_and_unavailable_ledger(db):
    with pytest.raises(QuotaRejected):
        QuotaGuard(db, clock=lambda: datetime(2026, 1, 1)).admit(cost(1))
    with db.connect() as con:
        con.execute("DROP TABLE automatic_quota")
    with pytest.raises(QuotaRejected, match="unavailable"):
        QuotaGuard(db).admit(cost(1))


def test_failed_attempts_and_retries_are_bounded_and_charged(db):
    fake = Fake()
    fake.fail = True
    wrapped = QuotaLimitedDailyProvider(fake, QuotaGuard(db), lambda **kw: cost(4))
    for expected in (1, 2, 3):
        with pytest.raises(RuntimeError, match="^automatic provider attempt failed$"):
            fetch(wrapped)
        assert fake.calls == expected  # no internal retry
    for _ in range(10):
        with pytest.raises(QuotaRejected):
            fetch(wrapped)
    assert fake.calls == 3
    assert usage(db) == 12


def test_concurrent_admission_cannot_overspend(db):
    def attempt(_):
        try:
            QuotaGuard(Database(db.path)).admit(cost(3))
            return True
        except QuotaRejected:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 5
    assert usage(db) == 15


def test_runtime_default_unknown_contract_and_budget_rejection(db, tmp_path):
    fake = Fake()
    arguments = dict(database=db, scheduler_repository=SchedulerRepository(db),
                     data_root=tmp_path / "data", provider=fake)
    runtime = build_daily_refresh_runtime(**arguments)
    with pytest.raises(QuotaRejected):
        fetch(runtime.provider)
    runtime = build_daily_refresh_runtime(**arguments,
                                         quota_cost_contract=lambda **kw: cost(16))
    with pytest.raises(QuotaRejected):
        fetch(runtime.provider)
    assert fake.calls == usage(db) == 0
    runtime = build_daily_refresh_runtime(**arguments,
                                         quota_cost_contract=lambda **kw: cost(3))
    assert fetch(runtime.provider)["symbol"] == "TEST"
    assert fake.calls == 1
    assert usage(db) == 3


def test_contract_failure_is_sanitized(db):
    def broken(**kw):
        raise ValueError("sensitive details")
    fake = Fake()
    with pytest.raises(QuotaRejected, match="^quota cost contract unavailable$"):
        fetch(QuotaLimitedDailyProvider(fake, QuotaGuard(db), broken))
    assert fake.calls == 0


def test_v7_upgrade_requires_permission_and_preserves_data(db):
    with db.connect() as con:
        con.execute("DROP TABLE automatic_quota")
        con.execute("UPDATE schema_meta SET value = '7' WHERE key = 'schema_version'")
        con.execute("INSERT INTO market_sessions VALUES ('2026-01-01', 'UNKNOWN', '{}', 'test')")
    with pytest.raises(RuntimeError, match="upgrade required"):
        db.initialize()
    assert db.schema_version() == 7
    db.initialize(allow_upgrade=True)
    assert db.schema_version() == 9
    assert usage(db) == 0
    with db.connect() as con:
        assert con.execute("SELECT status FROM market_sessions").fetchone()[0] == 'UNKNOWN'


def test_invalid_persisted_day_fails_closed(db):
    with db.connect() as con:
        con.execute("UPDATE automatic_quota SET quota_day = ''")
    with pytest.raises(QuotaRejected, match="invalid quota ledger"):
        QuotaGuard(db).admit(cost(1))
