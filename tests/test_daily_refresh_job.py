from datetime import date
from types import SimpleNamespace

import pytest

from app.data.daily_refresh_job import (
    DailyRefreshJob,
    DailyRefreshJobError,
    DailyRefreshTarget,
)


START = date(2026, 9, 1)
END = date(2026, 9, 9)
SNAPSHOT = date(2026, 9, 9)


class FakeIngestor:
    def __init__(self):
        self.calls = []

    def ingest(self, **kwargs):
        self.calls.append(
            (
                kwargs["canonical_symbol"],
                kwargs["provider_symbol"],
                kwargs["start_date"],
                kwargs["end_date"],
                kwargs["snapshot_date"],
            )
        )

        symbol = kwargs["canonical_symbol"]

        return SimpleNamespace(
            canonical_symbol=symbol,
            manifest=SimpleNamespace(
                ingestion_id=f"ing-{symbol}"
            ),
            record_count=3,
        )


class FakePipeline:
    def __init__(
        self,
        *,
        fail_symbol=None,
        fail_once=False,
    ):
        self.fail_symbol = fail_symbol
        self.fail_once = fail_once
        self.failed = False

    def finalize_ingestion(
        self,
        ingestion,
        *,
        canonical_store,
        repository,
    ):
        del canonical_store
        del repository

        symbol = ingestion.canonical_symbol

        if (
            symbol == self.fail_symbol
            and (
                not self.fail_once
                or not self.failed
            )
        ):
            self.failed = True
            raise RuntimeError("simulated failure")

        return SimpleNamespace(
            artifact_id=f"art-{symbol}",
            canonical_manifest=SimpleNamespace(
                valid_bar_count=3,
                quarantined_bar_count=0,
            ),
        )


def make_job(pipeline=None):
    ingestor = FakeIngestor()

    job = DailyRefreshJob(
        ingestor=ingestor,
        pipeline=pipeline or FakePipeline(),
        canonical_store=object(),
        artifact_repository=object(),
        targets=(
            DailyRefreshTarget(
                "comi",
                "comi.egx",
            ),
            DailyRefreshTarget(
                "east",
                "east.egx",
            ),
        ),
    )

    return job, ingestor


def run_job(job):
    return job.run(
        provider=object(),
        start_date=START,
        end_date=END,
        snapshot_date=SNAPSHOT,
    )


def test_success_preserves_target_order():
    job, ingestor = make_job()

    result = run_job(job)

    assert [
        item.canonical_symbol
        for item in result.items
    ] == ["COMI", "EAST"]

    assert result.items[0].artifact_id == "art-COMI"
    assert result.items[1].artifact_id == "art-EAST"

    assert [call[0] for call in ingestor.calls] == [
        "COMI",
        "EAST",
    ]


def test_partial_failure_reports_completed():
    job, _ = make_job(
        FakePipeline(
            fail_symbol="EAST",
        )
    )

    with pytest.raises(
        DailyRefreshJobError
    ) as captured:
        run_job(job)

    error = captured.value

    assert error.canonical_symbol == "EAST"
    assert error.cause_type == "RuntimeError"
    assert [
        item.canonical_symbol
        for item in error.completed
    ] == ["COMI"]


def test_retry_reuses_exact_inputs():
    pipeline = FakePipeline(
        fail_symbol="EAST",
        fail_once=True,
    )
    job, ingestor = make_job(pipeline)

    with pytest.raises(
        DailyRefreshJobError
    ):
        run_job(job)

    result = run_job(job)

    assert len(result.items) == 2

    expected = [
        ("COMI", "COMI.EGX", START, END, SNAPSHOT),
        ("EAST", "EAST.EGX", START, END, SNAPSHOT),
        ("COMI", "COMI.EGX", START, END, SNAPSHOT),
        ("EAST", "EAST.EGX", START, END, SNAPSHOT),
    ]

    assert ingestor.calls == expected


def test_duplicate_targets_fail_closed():
    with pytest.raises(
        ValueError,
        match="duplicate canonical_symbol",
    ):
        DailyRefreshJob(
            ingestor=object(),
            pipeline=object(),
            canonical_store=object(),
            artifact_repository=object(),
            targets=(
                DailyRefreshTarget(
                    "COMI",
                    "COMI.EGX",
                ),
                DailyRefreshTarget(
                    "COMI",
                    "OTHER.EGX",
                ),
            ),
        )


def test_invalid_dates_fail_closed():
    job, _ = make_job()

    with pytest.raises(
        ValueError,
        match="end_date",
    ):
        job.run(
            provider=object(),
            start_date=END,
            end_date=START,
            snapshot_date=SNAPSHOT,
        )

    with pytest.raises(
        ValueError,
        match="snapshot_date",
    ):
        job.run(
            provider=object(),
            start_date=START,
            end_date=END,
            snapshot_date=date(
                2026,
                9,
                8,
            ),
        )
