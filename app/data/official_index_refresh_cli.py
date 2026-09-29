from __future__ import annotations

import argparse
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.data.official_index_refresh_runtime import (
    build_official_index_refresh_runtime,
)
from app.data.providers.egx_official import (
    EGXOfficialPublicProvider,
)
from app.storage.database import Database


def main(argv: list[str] | None = None) -> int:
    """
    Reproducible CLI for the official-index refresh acquisition pass.

    Fetches the three required calendar-evidence indices (CASE30,
    EGX70_EWI, EGX100_EWI) from the reviewed anonymous public
    egx_official_public provider over the live network and admits the
    canonicalized result to the target database. Explicit
    --start-date/--end-date only: this is a manual acquisition tool,
    not a scheduled job, so it never infers a range.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Acquire and canonicalize the official EGX calendar-evidence "
            "indices over an explicit dated range from the live "
            "egx_official_public provider."
        )
    )
    parser.add_argument(
        "--start-date",
        required=True,
        type=date.fromisoformat,
    )
    parser.add_argument(
        "--end-date",
        required=True,
        type=date.fromisoformat,
    )
    parser.add_argument(
        "--snapshot-date",
        default=None,
        type=date.fromisoformat,
        help=(
            "Defaults to the current Africa/Cairo date, matching when "
            "this acquisition pass actually ran."
        ),
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=(
            "Defaults to the EGX_DB_PATH environment variable, "
            "then /app/data/platform.db."
        ),
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help=(
            "Root directory for immutable raw artifacts and the "
            "canonical index store. Defaults to the --db-path parent "
            "directory."
        ),
    )
    parser.add_argument(
        "--page-size",
        default=1000,
        type=int,
    )
    args = parser.parse_args(argv)

    db_path = args.db_path or os.getenv(
        "EGX_DB_PATH",
        "/app/data/platform.db",
    )

    data_root = (
        Path(args.data_root)
        if args.data_root
        else Path(db_path).parent
    )

    snapshot_date = args.snapshot_date or datetime.now(
        ZoneInfo("Africa/Cairo")
    ).date()

    database = Database(db_path)
    database.initialize()

    runtime = build_official_index_refresh_runtime(
        database=database,
        root=data_root,
    )

    provider = EGXOfficialPublicProvider()

    result = runtime.job.run(
        provider=provider,
        start_date=args.start_date,
        end_date=args.end_date,
        snapshot_date=snapshot_date,
        page_size=args.page_size,
    )

    for item in result.items:
        print(
            f"{item.index_name} "
            f"artifact={item.artifact_id} "
            f"records={item.record_count} "
            f"newest_market_date={item.newest_market_date}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
