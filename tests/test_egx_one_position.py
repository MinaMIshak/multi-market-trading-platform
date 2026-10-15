"""EGX one-position-per-ticker challenger (V2E, EGX-EXP-V2E-ONE-POSITION-v1): rule, isolation, forward-only scorecard."""
import copy
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app import egx_experiment_run as runner
from app.strategies import egx_experiment as exp
from app.strategies import egx_one_position as one
from app.strategies.egx_experiment_config import ACTIVE_CONFIG, ARMS, CONFIGS
from app.strategies.egx_ranking import Bar
from app.ui.experiment import cell, explanation, render_live, render_performance, render_system

CFG = CONFIGS[ACTIVE_CONFIG]


# --- the rule on hand-built V1 rows ---------------------------------------------------------------------------

def v1_row(ticker, session, events, *, status=None, net_r=None, eligibility="ELIGIBLE"):
    entry = next((d for d, e in events if e == "ENTRY_FILLED"), None)
    closed = status and status.startswith("CLOSED")
    return {"arm": "V1", "strategy": "EGX-EXP-v1", "config_version": ACTIVE_CONFIG, "ticker": ticker,
            "session": session, "eligibility": eligibility, "attribution": "PASS",
            "status": status or ("OPEN" if entry else "PENDING_ENTRY"),
            "events": [{"date": d, "event": e} for d, e in events], "entry_date": entry,
            "exit_date": events[-1][0] if closed else None, "net_r": net_r, "r_multiple_gross": None}


def cand(cid, ticker, session, score=70, generated_at="2026-10-02T05:39:00+00:00"):
    return {"candidate_id": cid, "ticker": ticker, "session": session, "classification": "CANDIDATE",
            "score": score, "generated_at": generated_at}


def scenario():
    """ABC: s1 opens; s2 and s3 repeat while it is open; it stops out on 10-07; s5 (10-07) may reopen. XYZ independent."""
    candidates = [cand("a1", "ABC", "2026-10-01"), cand("a2", "ABC", "2026-10-04"), cand("a3", "ABC", "2026-10-05"),
                  cand("a5", "ABC", "2026-10-07"), cand("x1", "XYZ", "2026-10-04")]
    v1 = {"a1": v1_row("ABC", "2026-10-01", [("2026-10-04", "ENTRY_FILLED"), ("2026-10-07", "EXIT_STOP")],
                       status="CLOSED_STOP", net_r=-1.05),
          "a2": v1_row("ABC", "2026-10-04", [("2026-10-05", "ENTRY_FILLED"), ("2026-10-07", "EXIT_STOP")],
                       status="CLOSED_STOP", net_r=-1.04),
          "a3": v1_row("ABC", "2026-10-05", [("2026-10-06", "ENTRY_FILLED"), ("2026-10-06", "PARTIAL_T1"),
                                             ("2026-10-11", "EXIT_T2")], status="CLOSED_T2", net_r=2.1),
          "a5": v1_row("ABC", "2026-10-07", [("2026-10-11", "ENTRY_FILLED")]),
          "x1": v1_row("XYZ", "2026-10-04", [("2026-10-05", "ENTRY_FILLED")])}
    return candidates, v1


def test_first_candidate_opens_repeats_are_blocked_and_reopen_after_close():
    candidates, v1 = scenario()
    frozen = copy.deepcopy(v1)
    rows = one.book(candidates, v1, sample=one.RETRO)
    assert v1 == frozen                                                    # V1 rows are never modified
    assert rows["a1"]["attribution"] == "PASS" and rows["a1"]["status"] == "CLOSED_STOP"   # 1. first opens
    for cid in ("a2", "a3"):                                               # 2. no concurrent second position
        assert rows[cid]["status"] == "BLOCKED" and rows[cid]["attribution"] == one.BLOCK_CODE
        assert rows[cid]["events"] == [] and "entry_date" not in rows[cid]
    # 3. the repeated signal stays observable with its evidence and the position that blocked it
    assert rows["a2"]["technical_score"] == 70 and rows["a2"]["technical_class"] == "CANDIDATE"
    assert rows["a2"]["blocking_position"] == {"candidate_id": "a1", "signal_session": "2026-10-01",
                                               "entry_date": "2026-10-04", "state_at_decision": "OPEN"}
    assert rows["a5"]["attribution"] == "PASS" and rows["a5"]["status"] == "OPEN"          # 4. eligible after close
    assert rows["x1"]["attribution"] == "PASS" and rows["x1"]["status"] == "OPEN"          # 5. other tickers
    assert all(r["strategy"] == one.CHALLENGER and r["arm"] == "V2E" for r in rows.values())


def test_pending_entry_occupies_the_ticker_and_a_no_fill_frees_it():
    candidates = [cand("p1", "PND", "2026-10-01"), cand("p2", "PND", "2026-10-04"),
                  cand("n1", "NOF", "2026-10-01"), cand("n2", "NOF", "2026-10-04")]
    v1 = {"p1": v1_row("PND", "2026-10-01", []), "p2": v1_row("PND", "2026-10-04", []),
          "n1": v1_row("NOF", "2026-10-01", [("2026-10-04", "NO_FILL_GAP_DOWN")], status="NO_FILL_GAP_DOWN"),
          "n2": v1_row("NOF", "2026-10-04", [])}
    rows = one.book(candidates, v1, sample=one.RETRO)
    assert rows["p2"]["attribution"] == one.BLOCK_CODE
    assert rows["p2"]["blocking_position"]["state_at_decision"] == "PENDING_ENTRY"
    assert rows["n2"]["attribution"] == "PASS"


def test_state_uses_only_events_up_to_the_decision_session():
    row = {"events": [{"date": "2026-10-04", "event": "ENTRY_FILLED"}, {"date": "2026-10-07", "event": "EXIT_STOP"}]}
    assert one.state_as_of(row, "2026-10-01") == "PENDING_ENTRY"
    assert one.state_as_of(row, "2026-10-06") == "OPEN"        # the later stop is not known on 10-06
    assert one.state_as_of(row, "2026-10-07") == "TERMINAL"


def test_blocked_v1_rows_never_occupy_a_ticker():
    candidates = [cand("b1", "BLK", "2026-10-01"), cand("b2", "BLK", "2026-10-04")]
    v1 = {"b1": {**v1_row("BLK", "2026-10-01", []), "eligibility": "UNKNOWN", "attribution": "NOT_READY",
                 "status": "BLOCKED"},
          "b2": v1_row("BLK", "2026-10-04", [("2026-10-05", "ENTRY_FILLED")])}
    rows = one.book(candidates, v1, sample=one.RETRO)
    assert rows["b1"]["status"] == "BLOCKED" and rows["b2"]["attribution"] == "PASS"


def bar(day, close):
    c = Decimal(str(close))
    return Bar(day, c, c, c, c, Decimal(1000))


def test_blocked_duplicates_separate_captured_moves_from_incremental_winners_and_avoided_losses():
    candidates, v1 = scenario()
    rows = one.book(candidates, v1, sample=one.RETRO)
    bars = {"ABC": [bar("2026-10-04", 10), bar("2026-10-05", 10.5), bar("2026-10-06", 11), bar("2026-10-07", 9),
                    bar("2026-10-11", 12)]}
    result = one.duplicates(rows, v1, bars, opportunity={("2026-10-05", "ABC"): {"DF0": 70.0}})
    by_id = {d["candidate_id"]: d for d in result["items"]}
    assert by_id["a2"]["classification"] == "DUPLICATE_LOSS_AVOIDED"
    # a3 hit T1 on 10-06 while a1 was still held, so the portfolio already had the move.
    assert by_id["a3"]["classification"] == "MOVE_ALREADY_CAPTURED_BY_OPEN_POSITION"
    assert by_id["a3"]["opportunity_score"] == {"DF0": 70.0}
    assert by_id["a3"]["return_1s"] == round(11 / 10.5 - 1, 5) and by_id["a3"]["return_3s"] == round(12 / 10.5 - 1, 5)
    assert by_id["a3"]["existing_position"]["status_now"] == "CLOSED_STOP"
    # If the open position had already exited before the duplicate's winning event, the winner is incremental.
    v1["a1"] = v1_row("ABC", "2026-10-01", [("2026-10-04", "ENTRY_FILLED"), ("2026-10-05", "EXIT_STOP")],
                      status="CLOSED_STOP", net_r=-1.05)
    rows = one.book([candidates[0], cand("a3", "ABC", "2026-10-04")], v1, sample=one.RETRO)
    assert rows["a3"]["attribution"] == one.BLOCK_CODE
    later = one.duplicates(rows, v1, bars)["items"][0]
    assert later["classification"] == "INCREMENTAL_WINNER_EXPOSURE_BLOCKED"


def test_exposure_counts_positions_held_at_each_close():
    rows = {"a": {"ticker": "A", "entry_date": "2026-10-04", "exit_date": "2026-10-06"},
            "b": {"ticker": "A", "entry_date": "2026-10-05", "exit_date": None},
            "c": {"ticker": "B", "entry_date": "2026-10-05", "exit_date": None}}
    sizing = {k: {"status": "SIZED", "position_value_egp": "100", "planned_risk_egp": "5"} for k in rows}
    out = one.exposure(rows, sizing, {"a": "S1", "b": "S1", "c": "S2"}, ["2026-10-04", "2026-10-05", "2026-10-06"])
    assert out["max_simultaneous_positions"] == 3 and out["max_positions_same_ticker"] == 2
    assert out["max_gross_exposure_egp"] == "300" and out["max_single_ticker_exposure_egp"] == "200"
    assert out["max_open_planned_risk_egp"] == "15" and out["sessions"] == 3


def test_verdict_needs_forward_sample_and_multiple_regimes_and_never_promotes():
    good = {"V1": {"expectancy_r": -0.5, "profit_factor": 0.5}, "V2E": {"closed": 30, "expectancy_r": -0.1,
                                                                        "profit_factor": 0.9}}
    assert one.verdict(good, 19, ["A", "B"])["verdict"] == "INSUFFICIENT_SAMPLE"
    assert one.verdict({**good, "V2E": {**good["V2E"], "closed": 5}}, 60, ["A", "B"])["verdict"] == "INSUFFICIENT_SAMPLE"
    early = one.verdict(good, 30, ["A", "B"])
    assert early["verdict"] == "INSUFFICIENT_SAMPLE" and early["checkpoint"] == "CHECKPOINT_20"
    assert one.verdict(good, 60, ["A"])["verdict"] == "INSUFFICIENT_SAMPLE"              # one regime only
    assert one.verdict(good, 60, ["A", "B"])["verdict"] == "PROMOTION_CANDIDATE"
    lower_dd_worse_return = {**good, "V2E": {"closed": 30, "expectancy_r": -0.6, "profit_factor": 0.9}}
    assert one.verdict(lower_dd_worse_return, 120, ["A", "B"])["verdict"] == "DOES_NOT_BEAT_CHAMPION"


def test_scorecard_keeps_gross_and_net_explicit():
    trades = [{"status": "CLOSED_STOP", "net_r": -1.05, "r_multiple_gross": "-1", "eligibility": "ELIGIBLE",
               "attribution": "PASS", "events": [{"event": "EXIT_STOP"}], "exit_date": "2026-10-06", "ticker": "A",
               "mae_r": "-1", "mfe_r": "0.2", "sessions_held": 2, "net_r_stressed": -1.1, "entry_date": "2026-10-05"},
              {"status": "BLOCKED", "eligibility": "BLOCKED", "attribution": one.BLOCK_CODE, "events": []}]
    score = one.scorecard(trades, exp.performance)
    assert score["expectancy_r_gross"] == -1.0 and score["expectancy_r_net"] == -1.05
    assert "NET" in score["r_basis"] and "gross" in score["r_basis"]
    assert score["duplicate_ticker_blocks"] == 1 and score["candidate_signals"] == 2


# --- end to end through the runner with a fake repository -----------------------------------------------------------

def egx_days(n, end):
    days, day = [], end
    while len(days) < n:
        if day.weekday() not in (4, 5):
            days.append(day)
        day -= timedelta(days=1)
    return list(reversed(days))


def history(end=date(2026, 10, 1), n=80):
    return [{"date": d.isoformat(), "open": Decimal(str(90 + i * 0.15)), "high": Decimal(str(90 + i * 0.15)) * Decimal("1.01"),
             "low": Decimal(str(90 + i * 0.15)) * Decimal("0.99"), "close": Decimal(str(90 + i * 0.15)),
             "volume": Decimal(500_000)} for i, d in enumerate(egx_days(n, end))]


def ohlc(day, o, h, low, c):
    return {"date": day, "open": Decimal(str(o)), "high": Decimal(str(h)), "low": Decimal(str(low)),
            "close": Decimal(str(c)), "volume": Decimal(500_000)}


class FakeRepository:
    BARS = {}

    def __init__(self, *, database_path, data_root):
        pass

    def by_id(self, artifact_id):
        return artifact_id if artifact_id in self.BARS else None

    def load(self, artifact):
        return SimpleNamespace(bars=self.BARS[artifact])

    def latest_by_symbol(self, provider):
        return {ticker: ticker for ticker in self.BARS}


def plan(cid, ticker, session, close, generated_at, score=70):
    close = Decimal(str(close))
    return {"candidate_id": cid, "ticker": ticker, "session": session, "classification": "CANDIDATE", "score": score,
            "entry_zone": [str(close - Decimal("0.2")), str(close + Decimal("0.2"))], "stop": str(close - 3),
            "target_1": str(close + Decimal("4.5")), "target_2": str(close + 9), "freshness": "CURRENT",
            "artifact_id": ticker, "generated_at": generated_at, "rank_version": "EGX-RANK-v1", "isin": f"EG{ticker}"}


def world(monkeypatch, tmp_path):
    base = history()
    close = base[-1]["close"]                                       # 101.85 on 2026-10-01
    abc = base + [ohlc("2026-10-04", close, close + 1, close - Decimal("0.5"), close + Decimal("0.5")),
                  ohlc("2026-10-05", close + Decimal("0.5"), close + Decimal("0.7"), close, close + Decimal("0.55")),
                  ohlc("2026-10-06", close + Decimal("0.55"), close + Decimal("0.6"), 98, Decimal("98.5")),
                  ohlc("2026-10-07", Decimal("98.5"), Decimal("99.5"), Decimal("98.2"), 99)]
    xyz = [{**b} for b in abc]
    FakeRepository.BARS = {"ABC": abc, "XYZ": xyz}
    monkeypatch.setattr(runner, "ValidatedDailyArtifactRepository", FakeRepository)
    retro_time = "2026-10-05T05:39:00+00:00"
    candidates = [plan("a1", "ABC", "2026-10-01", close, "2026-10-02T05:39:00+00:00"),
                  plan("a2", "ABC", "2026-10-05", close + Decimal("0.55"), "2026-10-06T05:39:00+00:00"),
                  plan("x1", "XYZ", "2026-10-04", close + Decimal("0.5"), retro_time, score=60)]
    ledger = tmp_path / "system-candidates.jsonl"
    ledger.write_text("".join(json.dumps(c) + "\n" for c in candidates))
    report = tmp_path / "egx-ranking.json"
    report.write_text(json.dumps({"session": "2026-10-05", "universe_momentum_median_20_pct": -2.0, "lifecycles": [],
                                  "symbols": [{"ticker": "ABC", "classification": "CANDIDATE", "score": 70}]}))
    return candidates, ledger, report


def run(ledger, report, state, now):
    return runner.run(db_path="/x/db", data_root="/x/data", candidates_path=ledger, ranking_path=report,
                      state_dir=state, now=now, build_revision="b" * 40)


def test_runner_keeps_v1_to_v2d_unchanged_and_reports_v2e_separately(monkeypatch, tmp_path):
    candidates, ledger, report_path = world(monkeypatch, tmp_path)
    frozen = ledger.read_text()
    state = tmp_path / "experiment"
    report = run(ledger, report_path, state, datetime(2026, 10, 6, 6, 0, tzinfo=timezone.utc))
    assert ledger.read_text() == frozen                             # 11. the V1 candidate ledger is not rewritten
    decisions = [json.loads(line) for line in (state / "decisions.jsonl").read_text().splitlines()]
    rows = runner.evaluate([json.loads(line) for line in frozen.splitlines()], decisions,
                           {t: runner._bars(SimpleNamespace(bars=b)) for t, b in FakeRepository.BARS.items()}, CFG)
    for arm in ARMS:                                                # 6.-10. V1, V2A, V2B, V2C, V2D unchanged
        assert report["rows"][arm] == json.loads(json.dumps(list(rows[arm].values()), default=str))
        assert {k: v for k, v in report["performance"][arm].items()          # V2D adds its EGP P&L fields
                if k not in ("pnl_egp_closed", "return_on_model_capital_pct")} == exp.performance(
            list(rows[arm].values()), eligible=sum(1 for t in rows[arm].values() if t["eligibility"] == "ELIGIBLE"),
            blocked=sum(1 for t in rows[arm].values() if t["status"] == "BLOCKED"))
    assert set(report["attribution"]) == {"V2A", "V2B", "V2C", "V2D", "V2E"}
    v2e = {r["session"] + r["ticker"]: r for r in report["rows"]["V2E"]}
    assert v2e["2026-10-05ABC"]["attribution"] == one.BLOCK_CODE     # a1 still open at the 10-05 decision
    assert v2e["2026-10-04XYZ"]["attribution"] == "PASS"
    assert report["current"]["ABC"]["one_position"]["status"] == "BLOCKED"
    expected = runner.current_view(json.loads(report_path.read_text()), decisions, rows)
    assert {t: {k: v for k, v in item.items() if k != "one_position"} for t, item in report["current"].items()} == \
        json.loads(json.dumps(expected, default=str))                   # the V2D view is unchanged
    assert report["live_money"] is False                            # 14.
    challenger = report["challengers"]["V2E"]
    assert challenger["promotion"] == "NOT_PROMOTED" and challenger["registration"]["live_money"] is False
    assert challenger["retrospective"]["sample"] == one.RETRO
    assert challenger["forward"]["status"] == "WAITING_FOR_FIRST_FORWARD_SESSION"


def test_forward_scorecard_starts_only_after_deployment_and_never_backfills(monkeypatch, tmp_path):
    candidates, ledger, report_path = world(monkeypatch, tmp_path)
    state = tmp_path / "experiment"
    deployed = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)
    registration = runner.register_v2e(state, candidates, now=deployed, build_revision="c" * 40,
                                       mode="EXPLICIT_AT_DEPLOYMENT")
    assert registration["last_retrospective_session"] == "2026-10-05" and registration["first_forward_session"] is None
    first = run(ledger, report_path, state, deployed + timedelta(minutes=5))
    assert first["challengers"]["V2E"]["forward"]["sessions"] == 0   # 10-05 is not after the last retrospective one
    # Next chain: a new session (10-06) and a candidate generated after deployment. A late-arriving record for the
    # same session that was generated before deployment must stay retrospective (no backfill).
    extra = [plan("a3", "ABC", "2026-10-06", Decimal("98.5"), "2026-10-07T05:39:00+00:00"),
             plan("x2", "XYZ", "2026-10-06", Decimal("98.5"), "2026-10-06T08:00:00+00:00")]
    with ledger.open("a") as stream:
        stream.write("".join(json.dumps(c) + "\n" for c in extra))
    report_path.write_text(json.dumps({"session": "2026-10-06", "universe_momentum_median_20_pct": 1.0,
                                       "lifecycles": [], "symbols": []}))
    second = run(ledger, report_path, state, datetime(2026, 10, 7, 5, 40, tzinfo=timezone.utc))
    forward = second["challengers"]["V2E"]["forward"]
    assert forward["first_forward_session"] == "2026-10-06" and forward["sessions"] == 1
    assert forward["candidates"] == 1 and forward["regimes"] == ["RISING_BREADTH"]
    samples = {r["session"] + r["ticker"]: r["sample"] for r in second["rows"]["V2E"]}
    assert samples["2026-10-06ABC"] == one.FORWARD and samples["2026-10-06XYZ"] == one.RETRO
    assert samples["2026-10-01ABC"] == one.RETRO
    # The forward book starts flat: the retrospective a1 position (stopped on 10-06) cannot block a3 there.
    forward_row = next(r for r in second["rows"]["V2E"] if r["session"] == "2026-10-06" and r["ticker"] == "ABC")
    assert forward_row["attribution"] == "PASS"
    assert forward["scorecard"]["V1"]["candidate_signals"] == 1 and forward["verdict"]["verdict"] == "INSUFFICIENT_SAMPLE"
    # 11. the registration is written once and never rewritten, except for the first forward session.
    again = runner.register_v2e(state, [], now=datetime(2027, 1, 1, tzinfo=timezone.utc), build_revision="d" * 40,
                                mode="AUTO_AT_FIRST_RUN")
    assert again["deployed_at"] == deployed.isoformat() and again["build_revision"] == "c" * 40
    assert again["first_forward_session"] == "2026-10-06"
    third = run(ledger, report_path, state, datetime(2026, 10, 7, 6, 0, tzinfo=timezone.utc))
    assert third["challengers"]["V2E"]["forward"]["sessions"] == 1    # the same session is not logged twice


def test_register_only_cli_and_live_money(tmp_path, capsys):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(cand("a1", "ABC", "2026-10-14")) + "\n")
    assert runner.main(["--register-v2e-only", "--candidates", str(ledger), "--state", str(tmp_path / "s")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "REGISTERED" and out["live_money"] is False
    assert out["registration"]["registration"] == "EXPLICIT_AT_DEPLOYMENT"
    assert out["registration"]["last_retrospective_session"] == "2026-10-14"


def test_ui_shows_compact_challenger_scorecard_and_detail_line_without_touching_today(monkeypatch, tmp_path):
    candidates, ledger, report_path = world(monkeypatch, tmp_path)
    report = json.loads(json.dumps(run(ledger, report_path, tmp_path / "e",
                                       datetime(2026, 10, 6, 6, 0, tzinfo=timezone.utc)), default=str))
    html = render_performance(report)
    assert "One-position-per-ticker challenger" in html and one.CHALLENGER in html
    assert "INSUFFICIENT_SAMPLE" in html and "RETROSPECTIVE_DIAGNOSTIC" in html and "FORWARD_OUT_OF_SAMPLE" in html
    assert "<th class=\"num\">V2E</th>" in html
    view = report["current"]["ABC"]
    assert "One-position-per-ticker challenger (V2E):</b> BLOCKED — ALREADY_OPEN_POSITION" in explanation(view)
    assert "V2E" not in cell(view)[0] and "ALREADY_OPEN_POSITION" not in cell(view)[0]
    assert "V2E" in render_live(report) and "deployed_at" in render_system(report)
