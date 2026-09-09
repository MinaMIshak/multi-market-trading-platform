from datetime import datetime as RealDateTime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.core import scheduler_worker
from app.core.schedule import CheckpointName
from app.data.providers import eodhd


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


class FixedDateTime:
    @classmethod
    def now(cls, tz=None):
        value = RealDateTime(
            2026, 9, 10, 16, 15,
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
        FixedDateTime,
    )


def test_observe_needs_no_secret(
    tmp_path, monkeypatch, capsys
):
    prepare(monkeypatch)

    db = tmp_path / "platform.db"

    monkeypatch.setenv(
        "EGX_SCHEDULER_MODE", "observe"
    )
    monkeypatch.setenv(
        "EGX_CALENDAR_TRUTH", "UNVERIFIED"
    )
    monkeypatch.setenv(
        "EGX_DB_PATH", str(db)
    )
    monkeypatch.setenv(
        "EODHD_API_TOKEN_FILE",
        str(tmp_path / "missing"),
    )

    scheduler_worker.main()

    out = capsys.readouterr().out

    assert "mode=observe" in out
    assert "execution_enabled=no" in out
    assert "DAILY_REFRESH_DISPATCH" not in out
    assert db.is_file()


def test_paper_unverified_no_network(
    tmp_path, monkeypatch, capsys
):
    prepare(monkeypatch)

    secret = tmp_path / "token"
    secret.write_text("fake-test-token\n")
    secret.chmod(0o600)

    def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError(
            "network request must not occur"
        )

    monkeypatch.setattr(
        eodhd.request,
        "urlopen",
        forbidden,
    )

    monkeypatch.setenv(
        "EGX_SCHEDULER_MODE",
        "paper_refresh",
    )
    monkeypatch.setenv(
        "EGX_CALENDAR_TRUTH",
        "UNVERIFIED",
    )
    monkeypatch.setenv(
        "EGX_DB_PATH",
        str(tmp_path / "platform.db"),
    )
    monkeypatch.setenv(
        "EODHD_API_TOKEN_FILE",
        str(secret),
    )

    scheduler_worker.main()

    out = capsys.readouterr().out

    assert "mode=paper_refresh" in out
    assert "execution_enabled=yes" in out
    assert "DAILY_REFRESH_DISPATCH" not in out


def test_verified_calls_dispatcher_once(
    tmp_path, monkeypatch, capsys
):
    prepare(monkeypatch)

    provider = object()
    calls = []

    class Dispatcher:
        def dispatch(
            self, *, evaluation, provider
        ):
            calls.append((evaluation, provider))
            return (
                SimpleNamespace(
                    checkpoint_name=(
                        CheckpointName
                        .AFTER_SESSION_PRIMARY
                    ),
                    claimed=True,
                    succeeded=True,
                    item_count=5,
                    error_type=None,
                ),
            )

    monkeypatch.setattr(
        scheduler_worker,
        "build_scheduler_execution_context",
        lambda **kwargs: SimpleNamespace(
            execution_enabled=True,
            provider=provider,
            dispatcher=Dispatcher(),
        ),
    )

    monkeypatch.setenv(
        "EGX_SCHEDULER_MODE",
        "paper_refresh",
    )
    monkeypatch.setenv(
        "EGX_CALENDAR_TRUTH",
        "VERIFIED_TRADING_DAY",
    )
    monkeypatch.setenv(
        "EGX_DB_PATH",
        str(tmp_path / "platform.db"),
    )

    scheduler_worker.main()

    out = capsys.readouterr().out

    assert len(calls) == 1
    assert calls[0][1] is provider
    assert "DAILY_REFRESH_DISPATCH" in out


def test_unknown_mode_fails_closed(
    tmp_path, monkeypatch
):
    prepare(monkeypatch)

    monkeypatch.setenv(
        "EGX_SCHEDULER_MODE",
        "live_money",
    )
    monkeypatch.setenv(
        "EGX_DB_PATH",
        str(tmp_path / "platform.db"),
    )

    with pytest.raises(ValueError):
        scheduler_worker.main()
