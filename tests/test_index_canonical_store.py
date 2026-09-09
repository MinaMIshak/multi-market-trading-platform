from __future__ import annotations

import hashlib
import json

from datetime import date

import pytest

from app.data.index_canonical import (
    IndexBarSemanticClass,
    canonicalize_index_row,
)
from app.data.index_canonical_store import (
    CanonicalIndexStore,
    SEMANTIC_CONTRACT_VERSION,
    SERIALIZATION_FORMAT,
)


SNAPSHOT = date(
    2026,
    9,
    9,
)


def make_bar(
    row,
    *,
    page=1,
    source_row=1,
    sha="a" * 64,
):
    return canonicalize_index_row(
        row,
        index_name="CASE30",
        source_provider=(
            "egx_official_public"
        ),
        source_snapshot_date=(
            SNAPSHOT
        ),
        source_page_number=page,
        source_row_number=(
            source_row
        ),
        source_sha256=sha,
    )


def full_row(
    day,
    *,
    opening,
    high,
    low,
    close,
):
    return {
        "indexDay": (
            day
            + "T00:00:00"
        ),
        "indexOpen": opening,
        "high": high,
        "low": low,
        "indexClose": close,
        "change": 1,
        "changePer": 0.1,
    }


def legacy_row(
    day,
    *,
    reference,
    close,
):
    return {
        "indexDay": (
            day
            + "T00:00:00"
        ),
        "indexOpen": reference,
        "high": 0,
        "low": 0,
        "indexClose": close,
        "change": 1,
        "changePer": 0.1,
    }


def quarantine_row(
    day,
):
    return {
        "indexDay": (
            day
            + "T00:00:00"
        ),
        "indexOpen": 100,
        "high": 110,
        "low": 90,
        "indexClose": 110.01,
        "change": 1,
        "changePer": 0.1,
    }


def sample_bars():
    return [
        make_bar(
            legacy_row(
                "1998-01-04",
                reference=1000,
                close=998.39,
            ),
            page=7,
            source_row=10,
            sha="7" * 64,
        ),
        make_bar(
            full_row(
                "2026-09-08",
                opening=56627.83,
                high=56909.93,
                low=56174.30,
                close=56174.30,
            ),
            page=1,
            source_row=1,
            sha="1" * 64,
        ),
        make_bar(
            quarantine_row(
                "2005-09-21"
            ),
            page=6,
            source_row=75,
            sha="6" * 64,
        ),
    ]


def test_writer_creates_deterministic_artifact(
    tmp_path,
):
    store = CanonicalIndexStore(
        tmp_path / "canonical"
    )

    manifest = store.store(
        sample_bars()
    )

    path = (
        tmp_path
        / "canonical"
        / manifest.relative_path
    )

    assert path.exists()

    payload = path.read_bytes()

    assert (
        hashlib.sha256(
            payload
        ).hexdigest()
        == manifest.sha256
    )

    parsed = json.loads(
        payload.decode(
            "utf-8"
        )
    )

    assert len(parsed) == 3

    assert [
        row["market_date"]
        for row in parsed
    ] == [
        "1998-01-04",
        "2005-09-21",
        "2026-09-08",
    ]

    assert (
        manifest.record_count
        == 3
    )

    assert (
        manifest
        .full_ohlc_valid_count
        == 1
    )

    assert (
        manifest
        .legacy_close_reference_count
        == 1
    )

    assert (
        manifest
        .quarantined_anomaly_count
        == 1
    )

    assert (
        manifest
        .full_ohlc_usable_count
        == 1
    )

    assert (
        manifest
        .close_history_usable_count
        == 2
    )

    assert (
        manifest
        .semantic_contract_version
        == SEMANTIC_CONTRACT_VERSION
    )

    assert (
        manifest
        .serialization_format
        == SERIALIZATION_FORMAT
    )


def test_input_order_does_not_change_bytes(
    tmp_path,
):
    bars = sample_bars()

    first_store = (
        CanonicalIndexStore(
            tmp_path
            / "one"
        )
    )

    second_store = (
        CanonicalIndexStore(
            tmp_path
            / "two"
        )
    )

    first = first_store.store(
        bars
    )

    second = second_store.store(
        list(
            reversed(bars)
        )
    )

    first_bytes = (
        tmp_path
        / "one"
        / first.relative_path
    ).read_bytes()

    second_bytes = (
        tmp_path
        / "two"
        / second.relative_path
    ).read_bytes()

    assert (
        first_bytes
        == second_bytes
    )

    assert (
        first.sha256
        == second.sha256
    )


def test_same_content_is_idempotent(
    tmp_path,
):
    store = CanonicalIndexStore(
        tmp_path / "canonical"
    )

    first = store.store(
        sample_bars()
    )

    second = store.store(
        sample_bars()
    )

    assert (
        first.relative_path
        == second.relative_path
    )

    assert (
        first.sha256
        == second.sha256
    )

    files = list(
        (tmp_path / "canonical")
        .rglob("*.json")
    )

    assert len(files) == 1


def test_changed_content_cannot_overwrite_snapshot(
    tmp_path,
):
    store = CanonicalIndexStore(
        tmp_path / "canonical"
    )

    original = [
        make_bar(
            full_row(
                "2026-09-08",
                opening=100,
                high=110,
                low=90,
                close=105,
            )
        )
    ]

    changed = [
        make_bar(
            full_row(
                "2026-09-08",
                opening=100,
                high=110,
                low=90,
                close=106,
            )
        )
    ]

    store.store(
        original
    )

    with pytest.raises(
        FileExistsError,
        match=(
            "immutable canonical "
            "artifact conflict"
        ),
    ):
        store.store(
            changed
        )


def test_duplicate_market_dates_are_rejected(
    tmp_path,
):
    bar_one = make_bar(
        full_row(
            "2026-09-08",
            opening=100,
            high=110,
            low=90,
            close=105,
        ),
        source_row=1,
    )

    bar_two = make_bar(
        full_row(
            "2026-09-08",
            opening=101,
            high=111,
            low=91,
            close=106,
        ),
        source_row=2,
    )

    store = CanonicalIndexStore(
        tmp_path / "canonical"
    )

    with pytest.raises(
        ValueError,
        match="duplicate",
    ):
        store.store(
            [
                bar_one,
                bar_two,
            ]
        )


def test_mixed_snapshot_dates_are_rejected(
    tmp_path,
):
    first = make_bar(
        full_row(
            "2026-09-07",
            opening=100,
            high=110,
            low=90,
            close=105,
        )
    )

    second = canonicalize_index_row(
        full_row(
            "2026-09-08",
            opening=105,
            high=115,
            low=100,
            close=110,
        ),
        index_name="CASE30",
        source_provider=(
            "egx_official_public"
        ),
        source_snapshot_date=date(
            2026,
            9,
            10,
        ),
        source_page_number=1,
        source_row_number=2,
        source_sha256="b" * 64,
    )

    store = CanonicalIndexStore(
        tmp_path / "canonical"
    )

    with pytest.raises(
        ValueError,
        match="snapshot",
    ):
        store.store(
            [
                first,
                second,
            ]
        )


def test_quarantined_row_remains_unusable(
    tmp_path,
):
    bar = make_bar(
        quarantine_row(
            "2005-09-21"
        )
    )

    assert (
        bar.semantic_class
        == IndexBarSemanticClass
        .QUARANTINED_ANOMALY
    )

    store = CanonicalIndexStore(
        tmp_path / "canonical"
    )

    manifest = store.store(
        [bar]
    )

    assert (
        manifest
        .quarantined_anomaly_count
        == 1
    )

    assert (
        manifest
        .close_history_usable_count
        == 0
    )
