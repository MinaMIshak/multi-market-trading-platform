from __future__ import annotations

import json

from datetime import date

from app.data import security_master_refresh_cli
from app.data.provider import ProviderResponse
from app.data.security_master_refresh_cli import main
from app.data.security_master_refresh_job import (
    EGID_PROVIDER,
)
from app.storage import Database
from app.storage.security_master_repository import (
    SecurityMasterRepository,
)


DAY = date(2026, 9, 29)


def egid_record():
    return {
        "SYMBOL_CODE": "EGS73541C012",
        "ARB_NAME": "القلعة للاستثمارات المالية",
        "ENG_NAME": "QALAA For Financial Investments",
        "Reuters": "CCAP.CA",
        "eng_shortname": "Qalaa Holdings",
        "arb_shortname": "القلعة القابضة",
    }


class FakeEGIDProvider:
    name = EGID_PROVIDER

    def __init__(self) -> None:
        self.calls = 0

    def fetch_security_master(self):
        self.calls += 1
        records = [egid_record()]

        payload = json.dumps(
            records, ensure_ascii=False
        ).encode("utf-8")

        return ProviderResponse(
            payload=payload,
            filename="market-watch-names.json",
            source_uri=(
                "https://ticker.egidegypt.com/"
                "api/DelayedFeed/"
                "getAllMarketWatchNames"
            ),
            record_count=len(records),
            metadata={"access": "anonymous"},
        )


def test_main_refreshes_security_master_against_explicit_db_path(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(
        security_master_refresh_cli,
        "EGIDProvider",
        FakeEGIDProvider,
    )

    db_path = tmp_path / "platform.db"

    exit_code = main([
        "--db-path", str(db_path),
        "--data-root", str(tmp_path / "data"),
        "--snapshot-date", DAY.isoformat(),
    ])

    assert exit_code == 0

    output = capsys.readouterr().out
    assert EGID_PROVIDER in output
    assert "instruments=1" in output

    database = Database(db_path)
    resolved = SecurityMasterRepository(
        database
    ).resolve("CCAP", provider="canonical")

    assert resolved["canonical_ticker"] == "CCAP"


def test_main_defaults_data_root_to_db_path_parent(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        security_master_refresh_cli,
        "EGIDProvider",
        FakeEGIDProvider,
    )

    db_path = tmp_path / "platform.db"

    exit_code = main([
        "--db-path", str(db_path),
        "--snapshot-date", DAY.isoformat(),
    ])

    assert exit_code == 0
    assert (tmp_path / "raw").is_dir()


def test_main_defaults_snapshot_date_to_today(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        security_master_refresh_cli,
        "EGIDProvider",
        FakeEGIDProvider,
    )

    db_path = tmp_path / "platform.db"

    exit_code = main([
        "--db-path", str(db_path),
        "--data-root", str(tmp_path / "data"),
    ])

    assert exit_code == 0
