from datetime import date

import pytest

from app.data.models import (
    BarGranularity,
    DataAssetType,
)
from app.data.raw_store import (
    ImmutableRawStore,
)


def stored(tmp_path):
    store = ImmutableRawStore(
        tmp_path / "raw"
    )

    payload = b'{"trusted":"bytes"}'

    manifest = store.store_bytes(
        provider="eodhd",
        asset_type=DataAssetType.DAILY_BARS,
        payload=payload,
        filename="COMI.EGX-D1.json",
        market_date=date(2026, 9, 9),
        symbol="COMI",
        granularity=BarGranularity.D1,
        record_count=1,
    )

    return store, manifest, payload


def test_verified_read_returns_exact_bytes(
    tmp_path,
):
    store, manifest, payload = stored(
        tmp_path
    )

    assert (
        store.read_verified(manifest)
        == payload
    )


def test_verified_read_rejects_tampering(
    tmp_path,
):
    store, manifest, payload = stored(
        tmp_path
    )

    target = (
        store.root
        / manifest.raw_path
    )

    tampered = (
        b"X" + payload[1:]
    )

    assert len(tampered) == len(payload)

    target.write_bytes(tampered)

    with pytest.raises(
        RuntimeError,
        match="sha256 mismatch",
    ):
        store.read_verified(manifest)


def test_verified_read_rejects_missing_file(
    tmp_path,
):
    store, manifest, _ = stored(
        tmp_path
    )

    (
        store.root
        / manifest.raw_path
    ).unlink()

    with pytest.raises(
        FileNotFoundError,
        match="raw artifact missing",
    ):
        store.read_verified(manifest)


def test_verified_read_rejects_path_traversal(
    tmp_path,
):
    store, manifest, _ = stored(
        tmp_path
    )

    unsafe = manifest.model_copy(
        update={
            "raw_path": "../escape.json",
        }
    )

    with pytest.raises(
        ValueError,
        match="unsafe raw artifact path",
    ):
        store.read_verified(unsafe)


def test_raw_store_enforces_file_mode_0660(
    tmp_path,
):
    store, manifest, payload = stored(
        tmp_path
    )

    target = (
        store.root
        / manifest.raw_path
    )

    assert (
        target.stat().st_mode & 0o777
    ) == 0o660

    target.chmod(0o644)

    replay = store.store_bytes(
        provider=manifest.provider,
        asset_type=manifest.asset_type,
        payload=payload,
        filename=target.name,
        market_date=manifest.market_date,
        symbol=manifest.symbol,
        granularity=manifest.granularity,
        record_count=manifest.record_count,
    )

    assert replay.sha256 == manifest.sha256

    assert (
        target.stat().st_mode & 0o777
    ) == 0o660
