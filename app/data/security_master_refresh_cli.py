from __future__ import annotations

import argparse
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.data.providers.egid import (
    EGIDProvider,
)
from app.data.security_master_refresh_runtime import (
    build_security_master_refresh_runtime,
)
from app.storage.database import Database


def main(argv: list[str] | None = None) -> int:
    """
    Reproducible CLI for the EGID security-master
    refresh acquisition pass.

    Fetches the full EGID market-watch names snapshot
    (getAllMarketWatchNames) from the reviewed
    anonymous public egid provider over the live
    network and admits the canonicalized instrument
    identities to the target database. This is a
    manual acquisition tool, not a scheduled job.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Acquire and canonicalize the full EGID "
            "security-master snapshot from the live "
            "anonymous egid provider."
        )
    )
    parser.add_argument(
        "--snapshot-date",
        default=None,
        type=date.fromisoformat,
        help=(
            "Defaults to the current Africa/Cairo date, "
            "matching when this acquisition pass "
            "actually ran."
        ),
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=(
            "Defaults to the EGX_DB_PATH environment "
            "variable, then /app/data/platform.db."
        ),
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help=(
            "Root directory for immutable raw "
            "artifacts. Defaults to the --db-path "
            "parent directory."
        ),
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

    runtime = build_security_master_refresh_runtime(
        database=database,
        root=data_root,
    )

    provider = EGIDProvider()

    result = runtime.job.run(
        provider=provider,
        snapshot_date=snapshot_date,
    )

    print(
        f"provider={result.provider} "
        f"snapshot_date={result.snapshot_date} "
        f"records={result.record_count} "
        f"instruments={result.instrument_count} "
        f"sha256={result.artifact_sha256}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
