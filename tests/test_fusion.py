from datetime import date

from app.context.fusion import MAX_ADJ, MIN_ADJ, apply, overlay

AS_OF = date(2026, 10, 3)


def context(egx_risk="LOW", us_risk="HIGH", egx_regime="RISK_ON", us_regime="RISK_OFF", statements=None,
            disclosures=None):
    return {"generated_at": "2026-10-03T08:00:00+00:00",
            "regimes": {"risk": {"EGX": {"label": egx_risk}, "US": {"label": us_risk}},
                        "egx_equity": {"label": egx_regime}, "us_equity": {"label": us_regime}},
            "events": {"egx_disclosures": {"status": "AVAILABLE", "records": disclosures or []}},
            "fundamentals": {"egx_financial_statements": {"status": "AVAILABLE", "records": statements or []}}}


def statement(net, prior, published="2026-10-01T12:00:00+03:00"):
    return {"isin": "EGS1", "published_at": published, "parse_status": "PARSED", "net_result": str(net),
            "comparative_net_result": str(prior), "basis": "CONSOLIDATED", "period_end": "2026-06-30",
            "heading": "Results"}


def test_overlay_rules_bounds_and_catalysts():
    record = {"ticker": "A", "isin": "EGS1", "score": 70, "classification": "CANDIDATE"}
    fused = overlay(record, market="EGX", index=__import__("app.context.fusion", fromlist=["x"]).index_context(
        context(statements=[statement(120, 100)], disclosures=[{"isin": "EGS1", "published_at": "2026-10-02T10:00:00+03:00",
                                                               "heading": "Board decision"}])), as_of=AS_OF)
    assert fused["context_adjustment"] == 5 and fused["context_confidence"] == "HIGH"
    assert fused["context_score"] == 75 and {c["type"] for c in fused["catalysts"]} == {"RECENT_DISCLOSURE",
                                                                                       "EARNINGS_EVENT"}
    assert fused["fundamentals"]["net_result"] == "120"
    us = apply({"symbols": [{"ticker": "B", "score": 80, "classification": "STRONG_CANDIDATE"}]}, market="US",
               context=context(), as_of=AS_OF)["symbols"][0]["context"]
    assert us["context_adjustment"] == -10 and us["context_confidence"] == "LOW"
    assert MIN_ADJ <= us["context_adjustment"] <= MAX_ADJ
    loss = overlay(record, market="EGX", index=__import__("app.context.fusion", fromlist=["x"]).index_context(
        context(egx_risk="ELEVATED", statements=[statement(-5, 10, "2026-06-01T12:00:00+03:00")])), as_of=AS_OF)
    assert loss["context_adjustment"] == -3 and loss["catalysts"] == []


def test_context_never_changes_classification_and_handles_missing_context():
    report = {"symbols": [{"ticker": "A", "score": 90, "classification": "WATCHLIST"},
                          {"ticker": "B", "score": None, "classification": "NO_TRADE"}]}
    fused = apply(report, market="EGX", context=None, as_of=AS_OF)
    assert [s["classification"] for s in fused["symbols"]] == ["WATCHLIST", "NO_TRADE"]
    assert fused["symbols"][0]["context"]["context_confidence"] == "UNKNOWN"
    assert "context" not in fused["symbols"][1] and "context" not in report["symbols"][0]
    assert apply(None, market="EGX", context=None, as_of=AS_OF) is None
    unknown = apply(report, market="EGX", context=context(egx_risk="UNKNOWN"), as_of=AS_OF)
    assert unknown["symbols"][0]["context"]["context_confidence"] == "UNKNOWN"
