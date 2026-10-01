"""Rank EGX symbols, record system Paper/Shadow candidates, recompute lifecycles.

    python -m app.egx_ranking_run --db-path DB --data-root DATA \
        --report /abs/egx-ranking.json --candidates /abs/system-candidates.jsonl

For one provider (default ``tradingview_tvdatafeed_egx``; providers are never
mixed), per symbol:
- the latest VALIDATED daily artifact is read through the approved validated reader, its
  SHA-256 is checked against the artifact record, and only VALID_EXECUTABLE
  rows are used;
- freshness comes from the same verified-session rule as TODAY; admission and
  the licensing label come from the daily source registry;
- EGX-RANK-v1 classifies the symbol (app/strategies/egx_ranking.py).

STRONG_CANDIDATE / CANDIDATE records are appended once to the immutable JSONL
ledger (id = hash of symbol, session and rule version), human review optional.
Every recorded candidate's lifecycle is then recomputed from bars after its
session (app/paper/system_candidates.py), together with performance. The
database is only read. The outputs are the report (written atomically) and
the append-only ledger. Paper/Shadow only; LIVE_MONEY is disabled.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from app.data.official_calendar_maintenance import CAIRO, last_completed_session_date
from app.data.source_admission import ADMITTED, daily_source_admission, licensing_label
from app.data.validated_daily_repository import ValidatedDailyArtifactRepository
from app.data.twelve_data_refresh import latest_verified_session
from app.paper.system_candidates import CANDIDATE_CLASSES, performance, simulate
from app.strategies.egx_ranking import CLASSES, VERSION, Bar, classify

DEFAULT_PROVIDER = "tradingview_tvdatafeed_egx"
ORDER = {name: index for index, name in enumerate(CLASSES)}


def load_bars(repository: ValidatedDailyArtifactRepository, artifact: dict) -> tuple[list[Bar], int]:
    dataset = repository.load(artifact)
    return [Bar(b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"])
            for b in dataset.bars], dataset.quarantined


def _freshness(db_path: Path, as_of) -> dict:
    from app.ui.today import load_validated_daily_observations
    rows = load_validated_daily_observations(database_path=db_path, market_date=as_of) or []
    return {(r["canonical_symbol"], r["provider"], r["newest_market_date"]): r["freshness"] for r in rows}


def _read_ledger(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run(*, db_path: Path, data_root: Path, report_path: Path, candidates_path: Path,
        now: datetime, provider: str = DEFAULT_PROVIDER) -> dict:
    session = latest_verified_session(db_path, last_completed_session_date(now))
    if session is None:
        raise ValueError("NO_VERIFIED_SESSION")
    admission = daily_source_admission(provider, "EGX")
    licensing = licensing_label(admission.declaration)
    repository = ValidatedDailyArtifactRepository(database_path=db_path, data_root=data_root)
    artifacts = repository.latest_by_symbol(provider)
    fresh = _freshness(db_path, now.astimezone(CAIRO).date())
    records, bars_by_symbol = [], {}
    for ticker, artifact in sorted(artifacts.items()):
        warnings = ["SPLIT_ADJUSTED_SERIES"] if provider.startswith("tradingview") else []
        try:
            bars, quarantined = load_bars(repository, artifact)
        except (OSError, ValueError) as exc:
            records.append({"ticker": ticker, "classification": "NO_TRADE", "source": provider,
                            "rejection_reasons": [f"ARTIFACT_UNREADABLE:{type(exc).__name__}:{exc}"[:120]],
                            "data_warnings": warnings, "rank_version": VERSION})
            continue
        bars_by_symbol[ticker] = bars
        upto = [b for b in bars if b.date <= session.isoformat()]
        record = classify(ticker=ticker, company=artifact["name_en"], isin=artifact["isin"],
                          source=provider, licensing=licensing, admitted=admission.status == ADMITTED,
                          freshness=fresh.get((ticker, provider, artifact["newest_market_date"]), "UNKNOWN"),
                          session=session.isoformat(), bars=upto, quarantined_rows=quarantined,
                          warnings=tuple(warnings))
        record["artifact_id"] = artifact["artifact_id"]
        records.append(record)
    records.sort(key=lambda r: (ORDER.get(r["classification"], 9), -(r.get("score") or 0), r["ticker"]))
    generated_at = now.isoformat()
    ledger = _read_ledger(candidates_path)
    known = {item["candidate_id"] for item in ledger}
    new = []
    for record in records:
        if record["classification"] not in CANDIDATE_CLASSES:
            continue
        candidate_id = hashlib.sha256(f"{record['ticker']}|{session}|{VERSION}".encode()).hexdigest()[:24]
        if candidate_id in known:
            continue
        new.append({"candidate_id": candidate_id, "generated_at": generated_at, "session": session.isoformat(),
                    "origin": "SYSTEM_GENERATED", "human_review": "OPTIONAL_NOT_REVIEWED",
                    "live_money": False, "mode": "PAPER_SHADOW",
                    **{key: record.get(key) for key in (
                        "ticker", "company", "isin", "source", "licensing", "classification", "score",
                        "entry_zone", "entry_reference", "stop", "target_1", "target_2",
                        "risk_reward_t1", "risk_reward_t2", "rank_version", "artifact_id")}})
    if new:
        candidates_path.parent.mkdir(parents=True, exist_ok=True)
        with candidates_path.open("a") as stream:
            for item in new:
                stream.write(json.dumps(item, sort_keys=True) + "\n")
    lifecycles = []
    for candidate in ledger + new:
        later = [b for b in bars_by_symbol.get(candidate["ticker"], []) if b.date > candidate["session"]]
        lifecycles.append({"candidate_id": candidate["candidate_id"], "ticker": candidate["ticker"],
                           "session": candidate["session"], "classification": candidate["classification"],
                           **simulate(candidate, later)})
    report = {"schema": "egx-ranking-report-v1", "rank_version": VERSION, "generated_at": generated_at,
              "session": session.isoformat(), "provider": provider, "admission": admission.status,
              "admission_reason": admission.reason, "licensing": licensing, "live_money": False,
              "counts": dict(Counter(r["classification"] for r in records)),
              "symbols_ranked": len(records), "new_candidates": len(new),
              "symbols": records, "lifecycles": lifecycles, "performance": performance(lifecycles)}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_name(report_path.name + ".tmp")
    temporary.write_text(json.dumps(report, sort_keys=True) + "\n")
    os.replace(temporary, report_path)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    args = parser.parse_args(argv)
    for path in (args.db_path, args.data_root, args.report, args.candidates):
        if not path.is_absolute():
            parser.error("all paths must be absolute")
    try:
        report = run(db_path=args.db_path, data_root=args.data_root, report_path=args.report,
                     candidates_path=args.candidates, now=datetime.now(timezone.utc), provider=args.provider)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)[:200]}))
        return 1
    print(json.dumps({"status": "RANKED", "session": report["session"], "counts": report["counts"],
                      "new_candidates": report["new_candidates"], "admission": report["admission"],
                      "licensing": report["licensing"], "performance": report["performance"]},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
