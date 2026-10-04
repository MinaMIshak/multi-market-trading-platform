"""Classify the stored EGX equity scope through the existing scan coordinator.

    python -m app.egx_universe_scan --db-path /abs/platform.db --history-path /abs/egx-scan-history.json

Scope is every EQUITY ticker in the security master: attribution, not dated
exchange membership (``security-master-equity-universe``). No launch evidence
is attached here, so each symbol is classified through the existing legacy
SWING launch gates and reported EVIDENCE_BLOCKED, meaning
LAUNCH_EVIDENCE_NOT_ATTACHED (no reviewed launch configuration attaches
per-symbol evidence). This is a different scope from the EGX-RANK-v1 ranking,
which covers symbols with admitted primary daily series. ``reconciliation``
reports both scopes side by side so the counts are never confused. Nothing is verified, published or written to the database. The only
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


SCOPE_SEMANTICS = {
    "scope": "security-master equities (attribution, not dated membership)",
    "EVIDENCE_BLOCKED": "LAUNCH_EVIDENCE_NOT_ATTACHED: legacy SWING launch path; no per-symbol launch evidence",
    "not_the_ranking_scope": "EGX-RANK-v1 ranks symbols with admitted primary daily series; see reconciliation",
}


def reconciliation(db_path: Path, tickers) -> dict:
    """Security-master scope vs admitted primary series (read-only, approved reader)."""
    from app.data.validated_daily_repository import ValidatedDailyArtifactRepository
    try:
        latest = ValidatedDailyArtifactRepository(database_path=db_path, data_root=db_path.parent).latest_by_symbol(
            "tradingview_tvdatafeed_egx")
    except Exception:  # unreadable evidence: report UNKNOWN, never guess
        return {"status": "UNKNOWN"}
    tickers = set(tickers)
    with_series = tickers & set(latest)
    return {"status": "AVAILABLE", "security_master_equities": len(tickers),
            "with_admitted_primary_series": len(with_series),
            "without_admitted_primary_series": len(tickers - with_series),
            "primary_series_outside_security_master": len(set(latest) - tickers)}


def run(*, db_path: Path, history_path: Path) -> dict:
    for path in (db_path, history_path):
        if not path.is_absolute():
            raise ValueError("absolute paths required")
    if not db_path.is_file():
        raise ValueError("database missing")
    database = Database(str(db_path))
    config = universe_scan_configuration(SecurityMasterRepository(database),
                                         scope_reference=UNIVERSE_SCOPE_REFERENCE)
    report = scan_egx_scope(symbols=config.symbols, sources=config.sources,
                            source_errors=config.source_errors, database=database,
                            data_root=str(db_path.parent), scope_reference=config.scope_reference,
                            history_path=history_path)
    report["scope_semantics"] = SCOPE_SEMANTICS
    report["reconciliation"] = reconciliation(db_path, config.symbols)
    return report


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
                      "status_counts": report["status_counts"],
                      "scope_semantics": report.get("scope_semantics"),
                      "reconciliation": report.get("reconciliation"), "live_money": False},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
