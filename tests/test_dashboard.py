import re

from app.strategies.egx_ranking import apply_relative_strength
from app.ui.dashboard import (render_candidate_table, render_cards, render_insights, render_session_context,
                              summary_counts)
from app.ui.product import product_state, render_product


def record(ticker, cls, score, **extra):
    base = {"ticker": ticker, "company": ticker + " Co", "classification": cls, "score": score,
            "price": "10.00", "entry_zone": ["9.95", "10.05"], "stop": "9.70", "target_1": "10.45",
            "target_2": "10.90", "risk_reward_t1": "1.5", "risk_reward_t2": "3", "trend": "UP",
            "momentum_20_pct": 5.0, "volume_ratio_20": 1.6, "volume_confirmation": True,
            "liquidity_avg_traded_value_20": 5_000_000, "relative_strength_20_pp": 2.0,
            "history_bars": 600, "quarantined_rows": 0, "freshness": "CURRENT",
            "evidence_snapshot_date": "2026-10-02", "selection_reason": "uptrend; 20-session breakout",
            "rejection_reasons": [], "data_warnings": ["SPLIT_ADJUSTED_SERIES"]}
    base.update(extra)
    return base


REPORT = {
    "schema": "egx-ranking-report-v1", "rank_version": "EGX-RANK-v1", "generated_at": "2026-10-02T05:39:30+00:00",
    "session": "2026-10-01", "based_on_session": "2026-10-01", "next_expected_session": "2026-10-04",
    "next_session_basis": "EXPECTED: EGX Sunday-Thursday trading week; holiday status for this date not verified",
    "prepared_note": "Based on the Thursday 2026-10-01 close; prepared on Friday 2026-10-02 for evaluation "
                     "ahead of the next expected EGX session, Sunday 2026-10-04.",
    "provider": "tradingview_tvdatafeed_egx", "admission": "ADMITTED",
    "licensing": "NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED", "live_money": False,
    "counts": {"STRONG_CANDIDATE": 1, "CANDIDATE": 1, "WATCHLIST": 1, "NO_TRADE": 2}, "symbols_ranked": 5,
    "symbols": [record("AAA", "STRONG_CANDIDATE", 82), record("BBB", "CANDIDATE", 70),
                record("CCC", "WATCHLIST", 50),
                record("DDD", "NO_TRADE", 20, selection_reason=None, rejection_reasons=["DOWNTREND"]),
                {"ticker": "EEE", "classification": "NO_TRADE", "score": None, "rank_version": "EGX-RANK-v1",
                 "rejection_reasons": ["INSUFFICIENT_HISTORY"], "data_warnings": []}],
    "lifecycles": [], "performance": {"status": "INSUFFICIENT_SAMPLE"},
}


def operational():
    return {"configured": True, "available": True, "status": "PARTIAL", "observed_at": None,
            "symbols": [{"market": "EGX", "symbol": f"S{i}", "status": "UNKNOWN"} for i in range(5)]}


def page(section="TODAY", ranking=REPORT):
    return render_product(product_state(operational(), market="EGX", section=section, ranking=ranking))


def outside_details(html):
    return re.sub(r"<details>.*?</details>", "", html, flags=re.S)


def test_today_puts_session_context_cards_and_candidates_before_diagnostics():
    html = page()
    main = html.split("<main>", 1)[1]
    positions = [main.index(marker) for marker in (
        'aria-label="Session context"', 'aria-label="Summary"', 'aria-label="Quick insights"',
        'aria-label="EGX candidates and watchlist"',
        "Data coverage and readiness", "Operational receipt diagnostics")]
    assert positions == sorted(positions)
    assert "verification window" not in outside_details(main)
    assert 'Next expected session</div><div class="v">Sun 2026-10-04' in html and "LIVE MONEY DISABLED" in html
    assert "NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED" in html


def test_candidate_table_lists_candidates_and_watchlist_with_evidence_and_filters():
    html = render_candidate_table(REPORT, table_id="t")
    rows = re.findall(r'<tr data-class="([A-Z_]+)" data-search="([^"]*)">', html)
    assert rows == [("STRONG_CANDIDATE", "AAA AAA Co"), ("CANDIDATE", "BBB BBB Co"), ("WATCHLIST", "CCC CCC Co"),
                    ("NO_TRADE", "DDD DDD Co"), ("NO_TRADE", "EEE ")]
    for text in ("9.95–10.05", "9.70", "10.45", "10.90", "1.5 / 3", "1.6× ✓", "5.0M", "600 bars, 0 quarantined",
                 "uptrend; 20-session breakout", "SPLIT_ADJUSTED_SERIES"):
        assert text in html
    filters = re.findall(r'data-filter="([A-Z_]+)" aria-pressed="(true|false)"', html)
    assert filters == [("ACTIONABLE", "true"), ("ALL", "false"), ("STRONG_CANDIDATE", "false"),
                       ("CANDIDATE", "false"), ("WATCHLIST", "false"), ("NO_TRADE", "false")]
    assert 'type="search"' in html and 'data-sortable' in html and 'data-sort="num"' in html
    assert 'Actionable (3)' in html and 'All (5)' in html
    # Evidence is per-row and collapsed; numerics are right-aligned.
    assert html.count("<details><summary>details</summary>") == 5 and '<td class="num" data-v="82">' in html
    assert '<span class="tk">AAA</span><span class="co">AAA Co</span>' in html


def test_missing_engine_values_render_unknown_not_invented():
    html = render_candidate_table(REPORT, table_id="n", classes=("NO_TRADE",))
    eee = re.search(r'<tr data-class="NO_TRADE" data-search="EEE "><td data-v="EEE">.*?</tr>', html, re.S).group(0)
    assert eee.count(">UNKNOWN<") >= 10 and "INSUFFICIENT_HISTORY" in eee
    assert "DOWNTREND" in html


def test_without_report_counts_are_unknown_and_context_says_unavailable():
    assert summary_counts(None)["candidates"] is None
    assert ">UNKNOWN<" in render_cards(None)
    assert "UNAVAILABLE" in render_session_context(None) and "LIVE MONEY DISABLED" in render_session_context(None)
    assert "UNAVAILABLE: no ranking report" in page(ranking=None)


def test_summary_cards_count_from_report():
    counts = summary_counts(REPORT, [{"level": "admitted_symbols", "count": 5},
                                     {"level": "admitted_current_symbols", "count": 4}])
    assert counts == {"scanned": 5, "admitted": 5, "current": 4, "ranked": 4, "candidates": 2, "strong": 1}


def test_swing_and_pre_surge_lead_with_research_output():
    swing = outside_details(page("SWING").split("<main>", 1)[1])
    assert swing.index("EGX candidates and watchlist") < swing.index("System-generated Paper/Shadow candidates")
    pre = page("PRE-SURGE")
    assert "Volume and momentum expansion screen" in outside_details(pre)
    assert "PreSurgeV7 attested-scorer contract" in pre


def test_relative_strength_is_cross_sectional_against_the_median():
    rows = [{"momentum_20_pct": 1.0}, {"momentum_20_pct": 3.0}, {"momentum_20_pct": 10.0}, {}]
    assert apply_relative_strength(rows) == 3.0
    assert [r.get("relative_strength_20_pp") for r in rows] == [-2.0, 0.0, 7.0, None]
    assert apply_relative_strength([{}]) is None


def test_quick_insights_show_top_idea_counts_and_next_session():
    html = render_insights(REPORT)
    assert '<span class="tk">AAA</span>' in html and "score 82" in html and "entry 9.95–10.05" in html
    assert "2026-10-04" in html and "Sun · expected" in html
    empty = dict(REPORT, symbols=[r for r in REPORT["symbols"] if r["classification"] == "NO_TRADE"])
    assert "No symbol passed all candidate rules." in render_insights(empty)
    assert render_insights(None) == ""
