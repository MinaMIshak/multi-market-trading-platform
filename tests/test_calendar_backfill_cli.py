from __future__ import annotations

from datetime import date

from app.core.calendar_backfill import main
from app.domain.enums import MarketSessionStatus
from app.storage import Database
from app.storage.repository import TradingRepository


FRIDAY = date(2026, 9, 11)
SATURDAY = date(2026, 9, 12)


def test_main_backfills_range_against_explicit_db_path(tmp_path, capsys):
    db_path = tmp_path / "platform.db"

    exit_code = main([
        "--db-path", str(db_path),
        "--start-date", FRIDAY.isoformat(),
        "--end-date", SATURDAY.isoformat(),
    ])

    assert exit_code == 0

    database = Database(db_path)
    trading_repository = TradingRepository(database)
    assert (
        trading_repository.get_market_session(FRIDAY).status
        == MarketSessionStatus.WEEKEND
    )
    assert (
        trading_repository.get_market_session(SATURDAY).status
        == MarketSessionStatus.WEEKEND
    )

    output = capsys.readouterr().out
    assert str(FRIDAY) in output
    assert "WEEKEND" in output


def test_main_rejects_inverted_range(tmp_path):
    db_path = tmp_path / "platform.db"

    try:
        main([
            "--db-path", str(db_path),
            "--start-date", SATURDAY.isoformat(),
            "--end-date", FRIDAY.isoformat(),
        ])
    except ValueError as exc:
        assert "end_date" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_main_requires_explicit_dates(tmp_path):
    db_path = tmp_path / "platform.db"

    try:
        main(["--db-path", str(db_path)])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("expected SystemExit for missing required arguments")


def test_main_falls_back_to_egx_db_path_env_when_no_flag(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "env-platform.db"
    monkeypatch.setenv("EGX_DB_PATH", str(db_path))

    exit_code = main([
        "--start-date", FRIDAY.isoformat(),
        "--end-date", FRIDAY.isoformat(),
    ])

    assert exit_code == 0
    database = Database(db_path)
    trading_repository = TradingRepository(database)
    assert (
        trading_repository.get_market_session(FRIDAY).status
        == MarketSessionStatus.WEEKEND
    )
