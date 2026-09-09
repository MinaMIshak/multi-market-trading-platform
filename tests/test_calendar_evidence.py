from datetime import date

from app.core.calendar_evidence import (
    OfficialIndexEvidenceRepository,
)


DAY = date(2026, 9, 10)


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return self

    def fetchall(self):
        return self.rows


class FakeDatabase:
    def __init__(self, rows):
        self.connection = FakeConnection(rows)

    def connect(self):
        return self.connection


def test_load_maps_validated_artifacts():
    rows = [
        {
            "provider": "egx_official_public",
            "symbol": "CASE30",
            "source_snapshot_date": "2026-09-10",
            "newest_market_date": "2026-09-10",
            "status": "VALIDATED",
        }
    ]

    db = FakeDatabase(rows)

    evidence = OfficialIndexEvidenceRepository(
        db
    ).load(DAY)

    assert len(evidence) == 1
    assert evidence[0].index_name == "CASE30"
    assert evidence[0].validated is True
    assert evidence[0].newest_market_date == DAY


def test_load_preserves_invalid_status():
    rows = [
        {
            "provider": "egx_official_public",
            "symbol": "CASE30",
            "source_snapshot_date": "2026-09-10",
            "newest_market_date": None,
            "status": "QUARANTINED",
        }
    ]

    evidence = OfficialIndexEvidenceRepository(
        FakeDatabase(rows)
    ).load(DAY)

    assert evidence[0].validated is False
    assert evidence[0].newest_market_date is None


def test_query_is_target_date_and_official_only():
    db = FakeDatabase([])

    OfficialIndexEvidenceRepository(
        db
    ).load(DAY)

    assert db.connection.params == (
        "egx_official_public",
        "2026-09-10",
        "CASE30",
        "EGX70_EWI",
        "EGX100_EWI",
    )

    assert "source_snapshot_date = ?" in (
        db.connection.sql
    )
    assert "asset_type = 'INDEX_BARS'" in (
        db.connection.sql
    )
    assert "granularity = 'D1'" in (
        db.connection.sql
    )


def test_empty_result_is_allowed():
    evidence = OfficialIndexEvidenceRepository(
        FakeDatabase([])
    ).load(DAY)

    assert evidence == []
