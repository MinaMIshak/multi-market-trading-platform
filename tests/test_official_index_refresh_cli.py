from __future__ import annotations

import json

from datetime import date

from app.core.calendar_evidence import (
    OfficialIndexEvidenceRepository,
)
from app.data import official_index_refresh_cli
from app.data.official_index_refresh_cli import main
from app.data.official_index_refresh_job import (
    OFFICIAL_PROVIDER,
    REQUIRED_OFFICIAL_INDICES,
)
from app.data.provider import (
    ProviderBatchResponse,
    ProviderResponse,
)
from app.storage import Database


DAY = date(2026, 9, 21)


def index_row(market_date: date):
    return {
        "change": 5,
        "changePer": 5,
        "high": 110,
        "indexClose": 105,
        "indexDay": market_date.isoformat() + "T00:00:00",
        "indexOpen": 100,
        "low": 90,
    }


class FakeOfficialProvider:
    name = OFFICIAL_PROVIDER

    def __init__(self) -> None:
        self.calls = []

    def fetch_index_bars(
        self,
        *,
        index_name,
        start_date,
        end_date,
        page_size=1000,
    ):
        self.calls.append((index_name, start_date, end_date, page_size))

        document = {
            "success": True,
            "data": [index_row(end_date)],
            "page": 1,
            "pageSize": page_size,
            "totalCount": 1,
            "totalPages": 1,
            "hasNextPage": False,
            "hasPreviousPage": False,
        }

        payload = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        response = ProviderResponse(
            payload=payload,
            filename=(
                f"{index_name}-{start_date.isoformat()}-"
                f"{end_date.isoformat()}-page-0001.json"
            ),
            source_uri="https://example.test/official-index",
            record_count=1,
            metadata={"page_number": 1},
        )

        return ProviderBatchResponse(
            responses=(response,),
            record_count=1,
            metadata={
                "provider": self.name,
                "index_name": index_name,
                "calculated_page_count": 1,
            },
        )


def test_main_refreshes_required_indices_against_explicit_db_path(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(
        official_index_refresh_cli,
        "EGXOfficialPublicProvider",
        FakeOfficialProvider,
    )

    db_path = tmp_path / "platform.db"

    exit_code = main([
        "--db-path", str(db_path),
        "--data-root", str(tmp_path / "data"),
        "--start-date", DAY.isoformat(),
        "--end-date", DAY.isoformat(),
        "--snapshot-date", DAY.isoformat(),
    ])

    assert exit_code == 0

    output = capsys.readouterr().out
    for index_name in REQUIRED_OFFICIAL_INDICES:
        assert index_name in output

    database = Database(db_path)
    evidence = OfficialIndexEvidenceRepository(database).load(DAY)
    assert len(evidence) == len(REQUIRED_OFFICIAL_INDICES)
    assert all(item.validated for item in evidence)
    assert {item.index_name for item in evidence} == set(
        REQUIRED_OFFICIAL_INDICES
    )


def test_main_defaults_data_root_to_db_path_parent(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        official_index_refresh_cli,
        "EGXOfficialPublicProvider",
        FakeOfficialProvider,
    )

    db_path = tmp_path / "platform.db"

    exit_code = main([
        "--db-path", str(db_path),
        "--start-date", DAY.isoformat(),
        "--end-date", DAY.isoformat(),
        "--snapshot-date", DAY.isoformat(),
    ])

    assert exit_code == 0
    assert (tmp_path / "raw").is_dir()


def test_main_rejects_inverted_range(tmp_path, monkeypatch):
    monkeypatch.setattr(
        official_index_refresh_cli,
        "EGXOfficialPublicProvider",
        FakeOfficialProvider,
    )

    db_path = tmp_path / "platform.db"

    try:
        main([
            "--db-path", str(db_path),
            "--start-date", DAY.isoformat(),
            "--end-date", date(2026, 9, 20).isoformat(),
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
        raise AssertionError(
            "expected SystemExit for missing required arguments"
        )
