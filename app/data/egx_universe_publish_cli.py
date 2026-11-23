from __future__ import annotations

import argparse
import os

from datetime import date
from pathlib import Path

from app.data.egx_universe_publisher import (
    publish_egx_universe,
)
from app.data.raw_store import (
    ImmutableRawStore,
)
from app.storage.database import (
    Database,
)
from app.storage.reference_repository import (
    ReferenceRepository,
)
from app.storage.security_master_repository import (
    SecurityMasterRepository,
)


def main(
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Publish an exact retained EGID "
            "security-master admission as "
            "candidate egx-universe-v1 evidence. "
            "This command does not approve or "
            "review the published universe."
        )
    )

    parser.add_argument(
        "--snapshot-date",
        required=True,
        type=date.fromisoformat,
        help=(
            "Exact admitted EGID snapshot date."
        ),
    )

    parser.add_argument(
        "--artifact-sha256",
        required=True,
        help=(
            "Exact admitted EGID artifact SHA-256."
        ),
    )

    parser.add_argument(
        "--db-path",
        default=None,
        help=(
            "Defaults to EGX_DB_PATH, then "
            "/app/data/platform.db."
        ),
    )

    parser.add_argument(
        "--data-root",
        default=None,
        help=(
            "Immutable reference artifact root. "
            "Defaults to the database parent."
        ),
    )

    args = parser.parse_args(argv)

    db_path = (
        args.db_path
        or os.getenv(
            "EGX_DB_PATH",
            "/app/data/platform.db",
        )
    )

    root = (
        Path(args.data_root)
        if args.data_root
        else Path(db_path).parent
    )

    database = Database(
        db_path
    )

    # Deliberately no allow_upgrade=True.
    # Production schema upgrades are a separate
    # controlled operation.
    database.initialize()

    security_master = (
        SecurityMasterRepository(
            database
        )
    )

    references = (
        ReferenceRepository(
            database,
            ImmutableRawStore(
                root
            ),
        )
    )

    result = publish_egx_universe(
        security_master=(
            security_master
        ),
        references=references,
        snapshot_date=(
            args.snapshot_date
        ),
        artifact_sha256=(
            args.artifact_sha256
        ),
    )

    print(
        "snapshot_date="
        f"{result.snapshot_date} "
        "artifact_sha256="
        f"{result.artifact_sha256} "
        "members="
        f"{result.member_count} "
        "eligible="
        f"{result.eligible_count} "
        "ineligible="
        f"{result.ineligible_count} "
        "universe_sha256="
        f"{result.universe_sha256} "
        "ingestion_id="
        f"{result.ingestion_id} "
        "live_money=false"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
