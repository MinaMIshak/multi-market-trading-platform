import hashlib
import stat
from datetime import date
from uuid import UUID

from app.data.daily_canonical import (
    DailyBarSemanticClass,
    canonicalize_daily_row,
)
from app.data.daily_canonical_store import (
    DailyCanonicalStore,
)


INSTRUMENT_ID = UUID(
    "4c1f3369-f71c-5856-aa52-953040a2cbc0"
)


def make_bar(
    market_date,
    *,
    source_row,
    source_sha,
    bad=False,
):
    row = {
        "date": market_date,
        "open": 150 if bad else 140,
        "high": 141,
        "low": 138,
        "close": 139,
        "adjusted_close": 139,
        "volume": 100,
    }

    return canonicalize_daily_row(
        row,
        instrument_id=INSTRUMENT_ID,
        canonical_symbol="COMI",
        provider_symbol="COMI.EGX",
        source_provider="eodhd",
        source_snapshot_date=date(2026, 9, 9),
        source_row_number=source_row,
        source_sha256=source_sha,
    )


def test_store_manifest_hash_counts_and_modes(
    tmp_path,
):
    store = DailyCanonicalStore(
        tmp_path / "canonical"
    )

    valid = make_bar(
        "2026-09-07",
        source_row=1,
        source_sha="a" * 64,
    )
    quarantined = make_bar(
        "2026-09-08",
        source_row=2,
        source_sha="b" * 64,
        bad=True,
    )

    manifest = store.store(
        [valid, quarantined]
    )

    target = (
        store.root
        / manifest.relative_path
    )

    payload = target.read_bytes()

    assert manifest.record_count == 2
    assert manifest.valid_bar_count == 1
    assert manifest.quarantined_bar_count == 1
    assert manifest.byte_size == len(payload)
    assert manifest.sha256 == (
        hashlib.sha256(payload).hexdigest()
    )

    assert stat.S_IMODE(
        target.stat().st_mode
    ) == 0o660

    assert stat.S_IMODE(
        target.parent.stat().st_mode
    ) == 0o2770

    assert quarantined.semantic_class == (
        DailyBarSemanticClass
        .QUARANTINED_ANOMALY
    )


def test_store_is_deterministic_and_idempotent(
    tmp_path,
):
    store = DailyCanonicalStore(
        tmp_path / "canonical"
    )

    first = make_bar(
        "2026-09-07",
        source_row=1,
        source_sha="a" * 64,
    )
    second = make_bar(
        "2026-09-08",
        source_row=2,
        source_sha="b" * 64,
    )

    a = store.store([first, second])
    b = store.store([second, first])

    assert a.relative_path == b.relative_path
    assert a.sha256 == b.sha256
    assert a.byte_size == b.byte_size


def test_store_rejects_immutable_conflict(
    tmp_path,
):
    from decimal import Decimal
    import pytest

    store = DailyCanonicalStore(
        tmp_path / "canonical"
    )

    original = make_bar(
        "2026-09-08",
        source_row=1,
        source_sha="a" * 64,
    )

    store.store([original])

    changed = original.model_copy(
        update={
            "volume": Decimal("101"),
        }
    )

    with pytest.raises(
        FileExistsError,
        match="immutable daily canonical",
    ):
        store.store([changed])


def test_store_rejects_mixed_provider_symbol(
    tmp_path,
):
    import pytest

    store = DailyCanonicalStore(
        tmp_path / "canonical"
    )

    first = make_bar(
        "2026-09-07",
        source_row=1,
        source_sha="a" * 64,
    )

    second = make_bar(
        "2026-09-08",
        source_row=2,
        source_sha="b" * 64,
    ).model_copy(
        update={
            "provider_symbol": "OTHER.EGX",
        }
    )

    with pytest.raises(
        ValueError,
        match="mix provider_symbol values",
    ):
        store.store([first, second])


def test_store_rejects_mixed_snapshot_date(
    tmp_path,
):
    import pytest

    store = DailyCanonicalStore(
        tmp_path / "canonical"
    )

    first = make_bar(
        "2026-09-07",
        source_row=1,
        source_sha="a" * 64,
    )

    second = make_bar(
        "2026-09-08",
        source_row=2,
        source_sha="b" * 64,
    ).model_copy(
        update={
            "source_snapshot_date": date(
                2026,
                9,
                10,
            ),
        }
    )

    with pytest.raises(
        ValueError,
        match="mix source_snapshot_date values",
    ):
        store.store([first, second])
