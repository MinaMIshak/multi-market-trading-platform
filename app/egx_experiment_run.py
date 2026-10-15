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

The separately versioned challenger V2E (EGX-EXP-V2E-ONE-POSITION-v1,
``app/strategies/egx_one_position.py``) is evaluated after V1-V2D from the V1
rows, which it does not modify. Its forward start is recorded once in
``<state>/v2e-registration.json``; ``--register-v2e-only`` writes that file at
deployment. Each later EGX report session is appended to
``<state>/v2e-forward-sessions.jsonl``. ``--fusion-dir`` and
``--market-watch-evidence`` are optional read-only inputs. They add Opportunity
scores and sectors to the duplicate and exposure diagnostics.
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
from app.strategies import egx_one_position as one
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


def attribution(rows, arms=ARMS[1:]):
    """What each arm excluded relative to V1 closed trades (winners vs losers, R)."""
    v1 = {cid: t for cid, t in rows["V1"].items() if t.get("net_r") is not None and t["status"].startswith("CLOSED")}
    out = {}
    for arm in arms:
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


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, default=str) + "\n")
    os.replace(temporary, path)


def register_v2e(state_dir, candidates, *, now, build_revision, mode):
    """Record the V2E forward start once (never overwritten): deployment time and last retrospective session."""
    path = Path(state_dir) / "v2e-registration.json"
    if path.is_file():
        return json.loads(path.read_text())
    registration = {
        "challenger": one.CHALLENGER, "arm": one.ARM, "config_version": one.CONFIG_VERSION,
        "registered_on": one.REGISTERED_ON, "deployed_at": now.isoformat(),
        "build_revision": build_revision or "UNKNOWN", "experiment": STRATEGY, "experiment_config": ACTIVE_CONFIG,
        "rank_version": next((c.get("rank_version") for c in reversed(candidates) if c.get("rank_version")), None),
        "data_version": f"{PROVIDER} validated daily artifacts (the V1 decision artifacts)",
        "last_retrospective_session": max((c["session"] for c in candidates), default=None),
        "first_forward_session": None, "registration": mode,
        "forward_rule": "FORWARD_OUT_OF_SAMPLE = candidates generated at or after deployed_at for EGX sessions on or "
                        "after first_forward_session (the first V1 report session after last_retrospective_session "
                        "evaluated after deployment); the forward book starts flat; nothing is backfilled.",
        "live_money": False}
    _write_json(path, registration)
    return registration


def forward_sessions(state_dir, registration, report_v1, *, now):
    """Append the V1 report session to the forward log if it is a new post-deployment session; set the first one."""
    path = Path(state_dir) / "v2e-forward-sessions.jsonl"
    log = _read(path)
    session, last = report_v1.get("session"), registration.get("last_retrospective_session")
    deployed = datetime.fromisoformat(registration["deployed_at"])
    if (session and (last is None or session > last) and now >= deployed
            and session not in {e["session"] for e in log}):
        entry = {"session": session, "recorded_at": now.isoformat(), "regime": one.regime(report_v1),
                 "universe_momentum_median_20_pct": report_v1.get("universe_momentum_median_20_pct")}
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")
        log.append(entry)
    if log and registration.get("first_forward_session") is None:
        registration = {**registration, "first_forward_session": min(e["session"] for e in log),
                        "first_forward_session_recorded_at": now.isoformat()}
        _write_json(Path(state_dir) / "v2e-registration.json", registration)
    return registration, sorted(log, key=lambda e: e["session"])


def _opportunity(fusion_dir):
    out = {}
    if not fusion_dir or not Path(fusion_dir).is_dir():
        return out
    for path in sorted(Path(fusion_dir).glob("*.json")):
        try:
            doc = json.loads(path.read_text())
            for ticker, item in (doc.get("scores") or {}).items():
                out[(doc["session"], ticker)] = item.get("opportunity_by_arm")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return out


def _sectors(candidates, evidence_root):
    if not evidence_root:
        return {}
    from app.learning.sources import egx_sectors
    by_isin = egx_sectors(evidence_root)
    return {c["candidate_id"]: by_isin.get(c.get("isin")) for c in candidates}


def challenger_v2e(candidates, decisions, rows, later_by_ticker, registration, log, *, fusion_dir=None,
                   evidence_root=None):
    """V2E rows (both books) and its retrospective and forward comparison with V1-V2D."""
    retro, forward = one.split(candidates, registration)
    v2e = {**one.book(retro, rows["V1"], sample=one.RETRO), **one.book(forward, rows["V1"], sample=one.FORWARD)}
    v2e = {cid: v2e[cid] for cid in rows["V1"]}
    all_rows = {**rows, one.ARM: v2e}
    sizing = {d["candidate_id"]: d.get("sizing") for d in decisions}
    sectors = _sectors(candidates, evidence_root)
    days = sorted({b.date for bars in later_by_ticker.values() for b in bars})
    opportunity = _opportunity(fusion_dir)

    def section(subset, sample):
        ids = {c["candidate_id"] for c in subset}
        arm_rows = {arm: {cid: r for cid, r in all_rows[arm].items() if cid in ids} for arm in all_rows}
        return {"sample": sample, "candidates": len(ids),
                "scorecard": {arm: one.scorecard(list(arm_rows[arm].values()), exp.performance) for arm in arm_rows},
                "duplicates": one.duplicates(arm_rows[one.ARM], rows["V1"], later_by_ticker, opportunity=opportunity),
                "exposure": {arm: one.exposure(arm_rows[arm], sizing, sectors, days) for arm in ("V1", "V2D", one.ARM)}}
    regimes = sorted({e["regime"] for e in log if e.get("regime") not in (None, "UNKNOWN")})
    forward_section = section(forward, one.FORWARD)
    forward_section.update(sessions=len(log), session_log=log, regimes=regimes,
                           first_forward_session=registration.get("first_forward_session"),
                           verdict=one.verdict(forward_section["scorecard"], len(log), regimes),
                           status="WAITING_FOR_FIRST_FORWARD_SESSION" if not log else "RECORDING")
    return v2e, {"challenger": one.CHALLENGER, "arm": one.ARM, "config_version": one.CONFIG_VERSION,
                 "definition": one.DEFINITION, "registration": registration, "promotion": "NOT_PROMOTED",
                 "promotion_policy": one.PROMOTION_POLICY, "checkpoints_sessions": list(one.CHECKPOINTS),
                 "retrospective": section(retro, one.RETRO), "forward": forward_section}


def run(*, db_path, data_root, candidates_path, ranking_path, state_dir, now=None, config_version=ACTIVE_CONFIG,
        fusion_dir=None, evidence_root=None, build_revision=None):
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
    registration = register_v2e(state_dir, candidates, now=now, build_revision=build_revision or os.environ.get(
        "EGX_BUILD_REVISION"), mode="AUTO_AT_FIRST_RUN")
    registration, log = forward_sessions(state_dir, registration, report_v1, now=now)
    v2e_rows, v2e_report = challenger_v2e(candidates, decisions, rows, later_by_ticker, registration, log,
                                          fusion_dir=fusion_dir, evidence_root=evidence_root)
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
    trades = list(v2e_rows.values())
    performance[one.ARM] = exp.performance(trades, eligible=sum(1 for t in trades if t["eligibility"] == "ELIGIBLE"),
                                           blocked=sum(1 for t in trades if t["status"] == "BLOCKED"))
    current = current_view(report_v1, decisions, rows)
    for row in v2e_rows.values():
        if row["session"] == report_v1["session"] and row["ticker"] in current:
            current[row["ticker"]]["one_position"] = {
                "challenger": one.CHALLENGER, "status": "BLOCKED" if row["status"] == "BLOCKED" else "PASS",
                "attribution": row.get("attribution"), "sample": row.get("sample")}
    report = {"schema": SCHEMA, "strategy": STRATEGY, "config_version": config_version,
              "config": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in cfg.items()},
              "arms": {**ARM_DEFINITIONS, one.ARM: one.DEFINITION + " Separately versioned: " + one.CHALLENGER + "."},
              "generated_at": now.isoformat(), "session": report_v1["session"],
              "live_money": False, "champion": "V1", "promotion_policy": (
                  "No auto-promotion. Compare expectancy, profit factor, drawdown and robustness at 20/50/100 closed "
                  "trades; win rate alone never decides."),
              "decisions": decisions, "rows": {**{arm: list(rows[arm].values()) for arm in ARMS},
                                               one.ARM: list(v2e_rows.values())},
              "performance": performance,
              "attribution": {**attribution(rows), **attribution({**rows, one.ARM: v2e_rows}, (one.ARM,))},
              "challengers": {one.ARM: v2e_report}, "current": current,
              "health": {"candidates": len(candidates), "decisions": len(decisions), "new_decisions": new,
                         "stale_decisions": sum(1 for d in decisions if d.get("stale")),
                         "tickers_without_latest_artifact": sorted(missing),
                         "v1_lifecycle_parity_mismatches": parity,
                         "status": "HEALTHY" if not parity and not missing else "ATTENTION"}}
    _write_json(state_dir / "egx-experiment.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("--db-path", "--data-root", "--candidates", "--ranking-report", "--state", "--fusion-dir",
                 "--market-watch-evidence"):
        parser.add_argument(name)
    parser.add_argument("--register-v2e-only", action="store_true",
                        help="record the V2E forward start (deployment) and exit; needs --candidates and --state")
    args = parser.parse_args(argv)
    needed = ("candidates", "state") if args.register_v2e_only else (
        "db_path", "data_root", "candidates", "ranking_report", "state")
    for name in needed:
        if not getattr(args, name):
            parser.error(f"--{name.replace('_', '-')} is required")
    for value in (args.db_path, args.data_root, args.candidates, args.ranking_report, args.state, args.fusion_dir,
                  args.market_watch_evidence):
        if value and not Path(value).is_absolute():
            parser.error("paths must be absolute")
    if args.register_v2e_only:
        registration = register_v2e(args.state, _read(args.candidates), now=datetime.now(timezone.utc),
                                    build_revision=os.environ.get("EGX_BUILD_REVISION"),
                                    mode="EXPLICIT_AT_DEPLOYMENT")
        print(json.dumps({"status": "REGISTERED", "registration": registration, "live_money": False}, sort_keys=True))
        return 0
    try:
        report = run(db_path=args.db_path, data_root=args.data_root, candidates_path=args.candidates,
                     ranking_path=args.ranking_report, state_dir=args.state, fusion_dir=args.fusion_dir,
                     evidence_root=args.market_watch_evidence)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)[:200], "live_money": False}))
        return 1
    print(json.dumps({"status": "EVALUATED", "health": report["health"],
                      "codes": {arm: report["attribution"][arm]["codes_all_candidates"]
                                for arm in ARMS[1:] + (one.ARM,)},
                      "v2e_forward": {k: report["challengers"][one.ARM]["forward"][k]
                                      for k in ("status", "sessions", "first_forward_session", "verdict")},
                      "live_money": False}, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
