from datetime import (
    date,
    datetime,
)
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from app.data import (
    DataAssetType,
    DataQualityEngine,
    ImmutableRawStore,
    MarketBar,
)


CAIRO = ZoneInfo("Africa/Cairo")


def test_raw_store_is_idempotent():
    with TemporaryDirectory() as tmp:
        store = ImmutableRawStore(
            Path(tmp)
        )

        payload = b"date,open,high,low,close\n"

        first = store.store_bytes(
            provider="test",
            asset_type=(
                DataAssetType.DAILY_BARS
            ),
            payload=payload,
            filename="SWDY.csv",
            market_date=date(
                2026,
                9,
                9,
            ),
            symbol="SWDY",
        )

        second = store.store_bytes(
            provider="test",
            asset_type=(
                DataAssetType.DAILY_BARS
            ),
            payload=payload,
            filename="SWDY.csv",
            market_date=date(
                2026,
                9,
                9,
            ),
            symbol="SWDY",
        )

        assert (
            first.sha256
            == second.sha256
        )


def test_quality_engine_detects_bad_range():
    bar = MarketBar(
        symbol="SWDY",
        timestamp=datetime(
            2026,
            9,
            9,
            10,
            0,
            tzinfo=CAIRO,
        ),
        open=Decimal("12"),
        high=Decimal("11"),
        low=Decimal("10"),
        close=Decimal("10.5"),
        volume=Decimal("1000"),
        source="test",
    )

    issues = (
        DataQualityEngine()
        .validate_bar(bar)
    )

    codes = {
        issue.code
        for issue in issues
    }

    assert "OPEN_OUTSIDE_RANGE" in codes
