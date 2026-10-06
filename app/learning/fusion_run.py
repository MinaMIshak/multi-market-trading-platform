"""Periodic Decision-Fusion walk-forward (DF0–DF5) and factor research, cached for the daily run.

    python -m app.learning.fusion_run --state /abs/learning --market EGX --egx-db ... --egx-data ... [--egx-evidence ...]
    python -m app.learning.fusion_run --state /abs/learning --market US --us-data ... --us-report ...

Writes ``<state>/<market>/fusion_walkforward.json`` atomically. It promotes
nothing: DF0 (technical) stays the champion until out-of-sample evidence and
an operator decision say otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.learning import fusion, fusion_eval, sources
from app.learning.daily import _write_atomic
from app.learning.dataset import FEATURE_SCHEMA, VERSION as DATA_VERSION, build, label_counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", required=True)
    parser.add_argument("--market", choices=("EGX", "US"), required=True)
    for name in ("--egx-db", "--egx-data", "--egx-evidence", "--us-data", "--us-report"):
        parser.add_argument(name)
    parser.add_argument("--build-revision", default=os.getenv("EGX_BUILD_REVISION"))
    args = parser.parse_args(argv)
    started = datetime.now(timezone.utc)
    series = (sources.load_egx(args.egx_db, args.egx_data, args.egx_evidence) if args.market == "EGX"
              else sources.load_us(args.us_data, args.us_report))
    ds = build(series)
    del series
    benchmarks = None
    if args.market == "US" and args.us_data:
        from app.us import benchmarks as us_benchmarks
        closes = us_benchmarks.load(args.us_data)
        benchmarks = {name: us_benchmarks.forward_returns(ds.sessions, series_closes)
                      for name, series_closes in closes.items()} or None
    evaluation = fusion_eval.run(ds, market=args.market, benchmarks=benchmarks)
    research = fusion_eval.factor_research(ds)
    gaps = fusion_eval.gap_research(ds) if args.market == "US" else None
    document = {"schema": "fusion-walkforward-v1", "market": args.market, "generated_at": started.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(), "build_revision": args.build_revision,
                "data_version": DATA_VERSION, "feature_schema": FEATURE_SCHEMA, "labels": label_counts(ds),
                "sessions": [ds.sessions[0], ds.sessions[-1]], "evaluation": evaluation, "factor_research": research, "gap_research": gaps,
                "benchmarks": sorted(benchmarks) if benchmarks else "UNAVAILABLE",
                "champion": "DF0" if args.market == "EGX" else "US-DF0", "promotion": "NONE: no auto-promotion; requires ≥ 50 sessions of out-of-sample "
                                                "superiority across regimes and an operator decision",
                "rationale": fusion.RATIONALE, "effective_date": fusion.EFFECTIVE_DATE}
    _write_atomic(Path(args.state) / args.market.lower() / "fusion_walkforward.json", document)
    print(json.dumps({"market": args.market, "rows": evaluation["evaluated_rows"],
                      "arms": {a: r["all"].get("spearman_mean") for a, r in evaluation["results"].items()},
                      "live_money": False}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
