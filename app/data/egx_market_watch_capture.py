"""Capture one official EGX market-watch snapshot as immutable blocked evidence.

    python -m app.data.egx_market_watch_capture --out-root /abs/evidence-dir

Writes ``<out-root>/<Cairo date>/<UTC stamp>/``: the raw market-status and
market-watch page bytes exactly as received, plus MANIFEST.json (SHA-256 per
file, counts, and the session-gate verdict of ``completed_session_bars``). The
directory is built privately, made read-only and renamed into place. It is
never overwritten. It writes no database and admits nothing: the source is
EVIDENCE_BLOCKED until its usage rights are reviewed. Exit 0 = captured
(whatever the gate verdict), 1 = nothing captured.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from app.data.providers.egx_market_watch import (
    CAIRO, EGXMarketWatchProvider, MarketWatchError, PROVIDER, completed_session_bars,
)

SCHEMA = "egx-market-watch-evidence-v1"


def capture(out_root: Path, provider=None) -> Path:
    if not out_root.is_absolute() or out_root.is_symlink():
        raise ValueError("absolute non-symlink evidence root required")
    snapshot = (provider or EGXMarketWatchProvider()).fetch_snapshot()
    try:
        session_date, bars, rejected = completed_session_bars(snapshot)
        gate = {"verdict": "COMPLETED_SESSION", "session_date": session_date.isoformat(),
                "bars": len(bars), "rejected": dict(sorted(Counter(rejected.values()).items()))}
    except MarketWatchError as exc:
        gate = {"verdict": "REJECTED", "reason": exc.code, "bars": 0}
    local = snapshot.captured_at.astimezone(CAIRO)
    target = out_root / local.date().isoformat() / snapshot.captured_at.strftime("%Y%m%dT%H%M%SZ")
    if target.exists():
        raise FileExistsError(f"evidence already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".partial-", dir=target.parent))
    try:
        files = []
        for response in (snapshot.status_response, *snapshot.pages):
            (staging / response.filename).write_bytes(response.payload)
            files.append({"file": response.filename, "source_uri": response.source_uri,
                          "bytes": len(response.payload),
                          "sha256": hashlib.sha256(response.payload).hexdigest()})
        manifest = {
            "schema": SCHEMA, "provider": PROVIDER,
            "entitlement": "NOT_ESTABLISHED", "admission": "EVIDENCE_BLOCKED",
            "captured_at": snapshot.captured_at.isoformat(), "cairo_time": local.isoformat(),
            "market_status": snapshot.status, "market_status_date": snapshot.status_date,
            "total_count": snapshot.total_count, "rows": len(snapshot.rows),
            "observation_times": {
                "capture_timestamp": snapshot.captured_at.isoformat(),
                "market_status_timestamp": snapshot.status_date,
                "source_write_dates": dict(sorted(Counter(str(r.get("writeTime"))[:8] for r in snapshot.rows).items())),
                "provider_last_trade_dates": dict(sorted(Counter(str(r.get("lastTradeDate"))[:10]
                                                                 for r in snapshot.rows).items())),
                "provider_last_trade_date_meaning": "date of prevClose (previous close), never the price session"},
            "session_gate": gate, "files": files,
            "note": "Blocked evidence only; not admitted data, not a candidate input.",
        }
        (staging / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        for path in staging.iterdir():
            path.chmod(0o444)
        staging.chmod(0o555)
        os.rename(staging, target)
    except BaseException:
        staging.chmod(0o700)
        for path in staging.iterdir():
            path.chmod(0o600)
        shutil.rmtree(staging)
        raise
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        target = capture(args.out_root)
    except (MarketWatchError, ValueError, FileExistsError) as exc:
        print(json.dumps({"status": "FAILED", "error": getattr(exc, "code", type(exc).__name__)}))
        return 1
    manifest = json.loads((target / "MANIFEST.json").read_text())
    print(json.dumps({"status": "CAPTURED", "path": str(target),
                      "session_gate": manifest["session_gate"], "rows": manifest["rows"]},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
