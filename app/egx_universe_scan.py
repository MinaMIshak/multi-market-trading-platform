"""Classify the stored EGX equity scope through the existing scan coordinator.

    python -m app.egx_universe_scan --db-path /abs/platform.db --history-path /abs/egx-scan-history.json

Scope is every EQUITY ticker in the security master: attribution, not dated
exchange membership (``security-master-equity-universe``). No launch evidence
is attached here, so each symbol is classified through the existing gates
and, while no daily source is admitted, is reported EVIDENCE_BLOCKED and not
scanned. Nothing is verified, published or written to the database. The only
output is the scan-history summary SYSTEM reads (a dated last-run record,
not current readiness).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.egx_scan import scan_egx_scope
from app.egx_scan_config import universe_scan_configuration
from app.egx_scan_dispatch import UNIVERSE_SCOPE_REFERENCE
from app.storage.database import Database
from app.storage.security_master_repository import SecurityMasterRepository


def run(*, db_path: Path, history_path: Path) -> dict:
    for path in (db_path, history_path):
        if not path.is_absolute():
            raise ValueError("absolute paths required")
    if not db_path.is_file():
        raise ValueError("database missing")
    database = Database(str(db_path))
    config = universe_scan_configuration(SecurityMasterRepository(database),
                                         scope_reference=UNIVERSE_SCOPE_REFERENCE)
    return scan_egx_scope(symbols=config.symbols, sources=config.sources,
                          source_errors=config.source_errors, database=database,
                          data_root=str(db_path.parent), scope_reference=config.scope_reference,
                          history_path=history_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--history-path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run(db_path=args.db_path, history_path=args.history_path)
    except Exception as exc:  # no paths or provider text in operator output
        print(json.dumps({"status": "FAILED", "error": type(exc).__name__}))
        return 1
    print(json.dumps({"status": "CLASSIFIED", "scope_reference": report["scope_reference"],
                      "requested": report["requested"], "scanned": report["scanned"],
                      "status_counts": report["status_counts"], "live_money": False},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
