"""Periodic walk-forward evaluation of the forecast champion and challengers (cached for the daily run).

    python -m app.learning.walkforward_run --state /abs/learning --market EGX --egx-db ... --egx-data ... [--egx-evidence ...]
    python -m app.learning.walkforward_run --state /abs/learning --market US --us-data ... --us-report ...

Writes ``<state>/<market>/walkforward.json`` atomically, with the build
revision, the data range, the models and the per-horizon metrics. The daily
run reads it; it never retrains on the evaluation folds. No model is promoted
here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.learning import sources
from app.learning.daily import LIQUIDITY_FLOOR, _write_atomic
from app.learning.dataset import FEATURE_SCHEMA, VERSION as DATA_VERSION, build, label_counts
from app.learning.models import CHAMPION
from app.learning.walkforward import MIN_TRAIN_SESSIONS, run as walk_forward


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
    dataset = build(series)
    results = walk_forward(dataset, liquidity_floor=LIQUIDITY_FLOOR[args.market])
    document = {"schema": "walkforward-v1", "market": args.market, "generated_at": started.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(), "build_revision": args.build_revision,
                "data_version": DATA_VERSION, "feature_schema": FEATURE_SCHEMA, "champion": CHAMPION,
                "min_train_sessions": MIN_TRAIN_SESSIONS, "fold": "calendar month, expanding window",
                "sessions": [dataset["sessions"][0], dataset["sessions"][-1]], "labels": label_counts(dataset),
                "models": results, "promotion": "NONE: comparison evidence only; operator decision"}
    _write_atomic(Path(args.state) / args.market.lower() / "walkforward.json", document)
    print(json.dumps({"market": args.market, "models": {k: v["predictions"] for k, v in results.items()},
                      "live_money": False}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
