from datetime import datetime as RealDateTime
from zoneinfo import ZoneInfo

import pytest

from app.core import scheduler_worker
from app.core.schedule import CheckpointName
from app.domain.enums import MarketSessionStatus
from app.storage import Database, TradingRepository
from app.storage.scheduler_repository import SchedulerRepository


CAIRO = ZoneInfo("Africa/Cairo")


class OneCycleEvent:
    def __init__(self):
        self.n = 0

    def is_set(self):
        self.n += 1
        return self.n > 1

    def wait(self, timeout):
        del timeout
        return True


class FridayMorning:
    @classmethod
    def now(cls, tz=None):
        value = RealDateTime(
            2026, 9, 11, 8, 31,
            tzinfo=CAIRO,
        )
        return (
            value
            if tz is not None
            else value.replace(tzinfo=None)
        )


def prepare(monkeypatch):
    monkeypatch.setattr(
        scheduler_worker.signal,
        "signal",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        scheduler_worker,
        "stop_event",
        OneCycleEvent(),
    )
    monkeypatch.setattr(
        scheduler_worker,
        "datetime",
        FridayMorning,
    )


def test_calendar_maintenance_defaults_disabled(
    tmp_path, monkeypatch, capsys
):
    prepare(monkeypatch)

    def forbidden(**kwargs):
        del kwargs
        raise AssertionError(
            "calendar runtime must stay disabled"
        )

    monkeypatch.setattr(
        scheduler_worker,
        "build_calendar_maintenance_runtime",
        forbidden,
    )

    monkeypatch.delenv(
        "EGX_CALENDAR_MAINTENANCE_MODE",
        raising=False,
    )
    monkeypatch.setenv(
        "EGX_SCHEDULER_MODE", "observe"
    )
    monkeypatch.setenv(
        "EGX_DB_PATH",
        str(tmp_path / "platform.db"),
    )
    monkeypatch.setenv(
        "EODHD_API_TOKEN_FILE",
        str(tmp_path / "missing"),
    )

    scheduler_worker.main()

    out = capsys.readouterr().out

    assert "calendar_maintenance=disabled" in out
    assert (
        "CALENDAR_MAINTENANCE_DISPATCH"
        not in out
    )


def test_local_calendar_runs_in_observe(
    tmp_path, monkeypatch, capsys
):
    prepare(monkeypatch)

    db_path = tmp_path / "platform.db"

    monkeypatch.setenv(
        "EGX_CALENDAR_MAINTENANCE_MODE",
        "local",
    )
    monkeypatch.setenv(
        "EGX_SCHEDULER_MODE", "observe"
    )
    monkeypatch.setenv(
        "EGX_DB_PATH", str(db_path)
    )
    monkeypatch.setenv(
        "EODHD_API_TOKEN_FILE",
        str(tmp_path / "missing"),
    )

    scheduler_worker.main()

    out = capsys.readouterr().out

    assert "calendar_maintenance=local" in out
    assert "execution_enabled=no" in out
    assert "CALENDAR_MAINTENANCE_DISPATCH" in out
    assert "VERIFIED_NON_TRADING_DAY" in out
    assert "DAILY_REFRESH_DISPATCH" not in out

    database = Database(db_path)

    market_date = RealDateTime(
        2026, 9, 11
    ).date()

    session = TradingRepository(
        database
    ).get_market_session(market_date)

    assert session is not None
    assert (
        session.status
        == MarketSessionStatus.WEEKEND
    )

    row = SchedulerRepository(
        database
    ).get_job(
        market_date=market_date,
        checkpoint_name=(
            CheckpointName.CALENDAR_MAINTENANCE
        ),
    )

    assert row is not None
    assert row["status"] == "SUCCEEDED"
    assert row["attempt_count"] == 1


def test_unknown_calendar_mode_fails_closed(
    tmp_path, monkeypatch
):
    prepare(monkeypatch)

    db_path = tmp_path / "platform.db"

    monkeypatch.setenv(
        "EGX_CALENDAR_MAINTENANCE_MODE",
        "network",
    )
    monkeypatch.setenv(
        "EGX_SCHEDULER_MODE", "observe"
    )
    monkeypatch.setenv(
        "EGX_DB_PATH", str(db_path)
    )

    with pytest.raises(
        ValueError,
        match="unsupported calendar maintenance mode",
    ):
        scheduler_worker.main()

    assert not db_path.exists()
