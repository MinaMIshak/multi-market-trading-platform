"""Run the EGX champion vs challenger experiment (EGX-EXP-v1) on the champion's own evidence.

    python -m app.egx_experiment_run --db-path DB --data-root DATA \
        --candidates /abs/egx-ranking/system-candidates.jsonl --ranking-report /abs/egx-ranking/egx-ranking.json \
        --state /abs/egx-experiment

Inputs are read-only: the V1 (EGX-RANK-v1) candidate ledger, the V1 report and the
validated daily artifacts. For each V1 candidate, decision-time evidence is
computed once from the exact artifact V1 used (``artifact_id``), with bars ≤ the
candidate session only, and appended to an immutable decision ledger keyed by
(candidate, config version). Every arm then sees the same candidates, bars,
session, timestamps and cost assumptions. Lifecycles are recomputed from the
latest validated bars after the session, exactly as V1 does. Nothing here
writes the V1 ledger or report. Paper/Shadow only; LIVE_MONEY=DISABLED.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.data.validated_daily_repository import ValidatedDailyArtifactRepository
from app.paper.system_candidates import COMMISSION, SLIPPAGE
from app.strategies import egx_experiment as exp
from app.strategies.egx_experiment_config import ACTIVE_CONFIG, ARM_DEFINITIONS, ARMS, CONFIGS, STRATEGY
from app.strategies.egx_ranking import Bar

SCHEMA = "egx-experiment-report-v1"
PROVIDER = "tradingview_tvdatafeed_egx"
NOT_FILLED = {"CHASE_BLOCKED": "CHASE_BLOCKED", "INVALIDATED": "INVALIDATED", "BELOW_ENTRY": "ENTRY_NOT_REACHED",
              "SETUP_EXPIRED": "SETUP_EXPIRED"}


def _bars(dataset):
    return [Bar(b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in dataset.bars]


def _read(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []


def record_decisions(candidates, repository, ledger_path, *, config_version=ACTIVE_CONFIG, now):
    cfg = CONFIGS[config_version]
    ledger = _read(ledger_path)
    known = {(d["candidate_id"], d["config_version"]) for d in ledger}
    new = []
    for candidate in candidates:
        if (candidate["candidate_id"], config_version) in known:
            continue
        artifact = repository.by_id(candidate["artifact_id"]) if candidate.get("artifact_id") else None
        if artifact is None:
            decision = {"candidate_id": candidate["candidate_id"], "ticker": candidate["ticker"],
                        "session": candidate["session"], "strategy": STRATEGY, "config_version": config_version,
                        "technical_class": candidate["classification"], "technical_score": candidate.get("score"),
                        "stale": "STALE_DATA: decision artifact unavailable", "artifact_id": candidate.get("artifact_id"),
                        "entry_zone": candidate["entry_zone"], "stop": candidate["stop"],
                        "liquidity": {"status": "UNKNOWN", "reason": "NOT_READY: artifact unavailable"},
                        "sizing": {"status": "UNKNOWN"},
                        "liquidity_gate": {"result": "UNKNOWN", "reason": "NOT_READY: artifact unavailable"},
                        "resistance_gate": {"result": "UNKNOWN", "reason": "NOT_READY: artifact unavailable"}}
        else:
            decision = exp.decide(candidate, _bars(repository.load(artifact)), cfg, config_version=config_version,
                                  strategy=STRATEGY)
        decision["decided_at"] = now.isoformat()
        new.append(decision)
    if new:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a") as stream:
            for item in new:
                stream.write(json.dumps(item, sort_keys=True) + "\n")
    return [d for d in ledger + new if d["config_version"] == config_version], len(new)


def _trade(candidate, later, decision):
    item = exp.lifecycle(candidate, later)
    stressed = exp.lifecycle(candidate, later, slippage=SLIPPAGE * 2, commission=COMMISSION * 2)
    return {**item, "net_r": exp.net_r(item, candidate["stop"]), "net_r_stressed": exp.net_r(stressed, candidate["stop"]),
            "exit_date": item["events"][-1]["date"] if item["events"] and item["status"].startswith("CLOSED") else None,
            "entry_date": next((e["date"] for e in item["events"] if e["event"] == "ENTRY_FILLED"), None)}


def evaluate(candidates, decisions, later_by_ticker, cfg, *, config_version=ACTIVE_CONFIG):
    """Per arm, per candidate: eligibility, attribution, execution state and lifecycle."""
    by_id = {d["candidate_id"]: d for d in decisions}
    rows = defaultdict(dict)
    ordered = sorted(candidates, key=lambda c: (c["session"], -(c.get("score") or 0), c["ticker"]))
    open_risk = []  # (entry_date, exit_date or None, planned risk) for V2D portfolio cap
    cap = cfg["model_capital_egp"] * cfg["max_open_risk_pct"] / 100
    for candidate in ordered:
        decision = by_id.get(candidate["candidate_id"])
        later = [b for b in later_by_ticker.get(candidate["ticker"], []) if b.date > candidate["session"]]
        for arm in ARMS:
            base = {"arm": arm, "strategy": STRATEGY, "config_version": config_version, "ticker": candidate["ticker"],
                    "session": candidate["session"], "technical_class": candidate["classification"],
                    "technical_score": candidate.get("score")}
            if decision is None:
                rows[arm][candidate["candidate_id"]] = {**base, "eligibility": "UNKNOWN", "attribution": "NOT_READY",
                                                         "status": "BLOCKED", "events": []}
                continue
            status, code = exp.eligibility(decision, arm)
            if status != "ELIGIBLE":
                rows[arm][candidate["candidate_id"]] = {**base, "eligibility": status, "attribution": code,
                                                         "status": "BLOCKED", "events": []}
                continue
            entry = None
            if arm in ("V2C", "V2D"):
                entry = exp.entry_state(decision, later, cfg)
                if entry["state"] in NOT_FILLED:
                    rows[arm][candidate["candidate_id"]] = {**base, "eligibility": status,
                                                             "attribution": NOT_FILLED[entry["state"]],
                                                             "status": entry["state"], "entry_state": entry, "events": []}
                    continue
            trade = _trade(candidate, later, decision)
            if arm == "V2D" and trade.get("entry_date"):
                planned = Decimal(decision["sizing"]["planned_risk_egp"])
                day = trade["entry_date"]
                current = sum(r for start, end, r in open_risk if start <= day and (end is None or end > day))
                if current + planned > cap:
                    rows[arm][candidate["candidate_id"]] = {**base, "eligibility": status,
                                                             "attribution": "PORTFOLIO_RISK_CAP", "status": "BLOCKED",
                                                             "open_risk_egp": str(current), "events": []}
                    continue
                open_risk.append((day, trade["exit_date"], planned))
                quantity = decision["sizing"]["quantity"]
                if trade.get("net_return_pct"):
                    trade["pnl_egp"] = str((Decimal(trade["entry"]) * quantity * Decimal(trade["net_return_pct"]) / 100)
                                           .quantize(Decimal("0.01")))
                trade["quantity"] = quantity
            rows[arm][candidate["candidate_id"]] = {**base, "eligibility": status, "attribution": "PASS",
                                                     "entry_state": entry, **trade}
    return rows


def attribution(rows):
    """What each arm excluded relative to V1 closed trades (winners vs losers, R)."""
    v1 = {cid: t for cid, t in rows["V1"].items() if t.get("net_r") is not None and t["status"].startswith("CLOSED")}
    out = {}
    for arm in ARMS[1:]:
        excluded = Counter()
        detail = defaultdict(lambda: {"v1_winners_excluded": 0, "v1_losers_avoided": 0, "v1_r_excluded": 0.0})
        for cid, trade in v1.items():
            row = rows[arm][cid]
            if row.get("attribution") == "PASS" and row.get("net_r") is not None:
                continue
            code = row.get("attribution")
            excluded[code] += 1
            bucket = detail[code]
            bucket["v1_winners_excluded" if trade["net_r"] > 0 else "v1_losers_avoided"] += 1
            bucket["v1_r_excluded"] = round(bucket["v1_r_excluded"] + trade["net_r"], 3)
        out[arm] = {"by_code": dict(detail), "codes_all_candidates": dict(Counter(r["attribution"]
                                                                                   for r in rows[arm].values()))}
    return out


def current_view(report, decisions, rows):
    """Per ticker for the V1 report's session: technical class vs V2D eligibility vs final action."""
    by_ticker = {d["ticker"]: d for d in decisions if d["session"] == report["session"]}
    v2d = {r["ticker"]: r for r in rows["V2D"].values() if r["session"] == report["session"]}
    view = {}
    for record in report["symbols"]:
        cls = record.get("classification")
        if cls in ("STRONG_CANDIDATE", "CANDIDATE") and record["ticker"] in v2d:
            row, decision = v2d[record["ticker"]], by_ticker.get(record["ticker"], {})
            status = row["status"]
            action = ("WATCH" if row["eligibility"] != "ELIGIBLE" or row["attribution"] == "PORTFOLIO_RISK_CAP" else
                      "CHASE_BLOCKED" if status == "CHASE_BLOCKED" else "INVALIDATED" if status == "INVALIDATED" else
                      "EXPIRED" if status in ("BELOW_ENTRY", "SETUP_EXPIRED") else "PAPER_ENTRY")
            view[record["ticker"]] = {"arm": "V2D", "technical_class": cls, "technical_score": record.get("score"),
                                      "eligibility": row["eligibility"], "attribution": row["attribution"],
                                      "final_action": action, "lifecycle_status": status,
                                      "liquidity_gate": decision.get("liquidity_gate"),
                                      "liquidity": decision.get("liquidity"),
                                      "resistance_gate": decision.get("resistance_gate"),
                                      "sizing": decision.get("sizing"), "entry_state": row.get("entry_state"),
                                      "config_version": decision.get("config_version")}
        elif cls == "WATCHLIST":
            view[record["ticker"]] = {"technical_class": cls, "final_action": "WATCH", "eligibility": "NOT_APPLICABLE"}
        else:
            view[record["ticker"]] = {"technical_class": cls, "final_action": "NO_TRADE", "eligibility": "NOT_APPLICABLE"}
    return view


def run(*, db_path, data_root, candidates_path, ranking_path, state_dir, now=None, config_version=ACTIVE_CONFIG):
    now = now or datetime.now(timezone.utc)
    cfg = CONFIGS[config_version]
    state_dir = Path(state_dir)
    repository = ValidatedDailyArtifactRepository(database_path=db_path, data_root=data_root)
    candidates = _read(candidates_path)
    report_v1 = json.loads(Path(ranking_path).read_text())
    decisions, new = record_decisions(candidates, repository, state_dir / "decisions.jsonl",
                                      config_version=config_version, now=now)
    latest = repository.latest_by_symbol(PROVIDER)
    later_by_ticker, missing = {}, []
    for ticker in {c["ticker"] for c in candidates}:
        if ticker in latest:
            later_by_ticker[ticker] = _bars(repository.load(latest[ticker]))
        else:
            missing.append(ticker)
    rows = evaluate(candidates, decisions, later_by_ticker, cfg, config_version=config_version)
    v1_report = {item["candidate_id"]: item for item in report_v1.get("lifecycles", [])}
    parity = [cid for cid, row in rows["V1"].items() if cid in v1_report
              and (row["status"], row.get("entry")) != (v1_report[cid]["status"], v1_report[cid].get("entry"))]
    performance = {}
    for arm in ARMS:
        trades = list(rows[arm].values())
        blocked = sum(1 for t in trades if t["status"] == "BLOCKED")
        performance[arm] = exp.performance(trades, eligible=sum(1 for t in trades if t["eligibility"] == "ELIGIBLE"),
                                           blocked=blocked)
        if arm == "V2D":
            pnl = [Decimal(t["pnl_egp"]) for t in trades if t.get("pnl_egp") and t["status"].startswith("CLOSED")]
            performance[arm]["pnl_egp_closed"] = str(sum(pnl)) if pnl else "0"
            performance[arm]["return_on_model_capital_pct"] = (
                str((sum(pnl) / cfg["model_capital_egp"] * 100).quantize(Decimal("0.001"))) if pnl else "0")
    report = {"schema": SCHEMA, "strategy": STRATEGY, "config_version": config_version,
              "config": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in cfg.items()},
              "arms": ARM_DEFINITIONS, "generated_at": now.isoformat(), "session": report_v1["session"],
              "live_money": False, "champion": "V1", "promotion_policy": (
                  "No auto-promotion. Compare expectancy, profit factor, drawdown and robustness at 20/50/100 closed "
                  "trades; win rate alone never decides."),
              "decisions": decisions, "rows": {arm: list(rows[arm].values()) for arm in ARMS},
              "performance": performance, "attribution": attribution(rows),
              "current": current_view(report_v1, decisions, rows),
              "health": {"candidates": len(candidates), "decisions": len(decisions), "new_decisions": new,
                         "stale_decisions": sum(1 for d in decisions if d.get("stale")),
                         "tickers_without_latest_artifact": sorted(missing),
                         "v1_lifecycle_parity_mismatches": parity,
                         "status": "HEALTHY" if not parity and not missing else "ATTENTION"}}
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "egx-experiment.json"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, sort_keys=True, default=str) + "\n")
    os.replace(temporary, path)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("--db-path", "--data-root", "--candidates", "--ranking-report", "--state"):
        parser.add_argument(name, required=True)
    args = parser.parse_args(argv)
    for value in (args.db_path, args.data_root, args.candidates, args.ranking_report, args.state):
        if not Path(value).is_absolute():
            parser.error("paths must be absolute")
    try:
        report = run(db_path=args.db_path, data_root=args.data_root, candidates_path=args.candidates,
                     ranking_path=args.ranking_report, state_dir=args.state)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)[:200], "live_money": False}))
        return 1
    print(json.dumps({"status": "EVALUATED", "health": report["health"],
                      "codes": {arm: report["attribution"][arm]["codes_all_candidates"] for arm in ARMS[1:]},
                      "live_money": False}, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
