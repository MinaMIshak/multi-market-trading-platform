"""Live Decision-Fusion scoring for a market's latest session (research; trade gates are applied outside the score).

EGX (EGX-DECISION-FUSION-v1) and US (US-DECISION-FUSION-v1) use the same machinery with separate
configurations, weights, factor groups, quant edges and forecasts. Each is fitted only on that
market's own dataset. The reported arm is the validated arm with the best out-of-sample 3-session
rank IC from the market's cached walk-forward. Without one, the technical arm is reported and
labelled as such.

US additions:
- fundamentals: cross-sectional percentile of sourced snapshot fields;
- earnings state from release dates on the NYSE calendar;
- catalyst: a recent earnings report counts only with price/volume confirmation;
- hard gates (``fusion.us_gates``) plus a per-sector concentration cap on actionable names.

A gate failure always blocks the Paper/Shadow action, whatever the Opportunity Score.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.learning import fusion
from app.learning.fusion_eval import LIQUIDITY_FLOOR


def best_arm(cache, market="EGX"):
    """Report a challenger only if it beats the technical arm out of sample.

    It needs a positive rank IC and a better Top-10 3-session return than the technical arm; ties go to the
    simpler arm. Otherwise the technical arm is reported (NO_CHALLENGER_BEATS_TECHNICAL).
    """
    config = fusion.MARKETS[market]
    results = ((cache or {}).get("evaluation") or {}).get("results") or {}
    base = list(config["arms"])[0]
    base_metrics = (results.get(base) or {}).get("all") or {}
    if base_metrics.get("status") != "OK":
        return base, "NO_VALIDATED_CHALLENGER (walk-forward not run or insufficient)", None
    order = {arm: k for k, arm in enumerate(config["arms"])}
    better = [(arm, r["all"]) for arm, r in results.items() if arm != base and (r.get("all") or {}).get("status") == "OK"
              and (r["all"].get("spearman_mean") or 0) > 0
              and (r["all"].get("top10_mean_r3") or 0) > (base_metrics.get("top10_mean_r3") or 0)]
    if not better:
        return base, "NO_CHALLENGER_BEATS_TECHNICAL (technical arm reported; challengers EXPERIMENTAL)", base_metrics
    arm, metrics = max(better, key=lambda item: (item[1]["top10_mean_r3"], -order.get(item[0], 99)))
    return arm, "VALIDATED_OUT_OF_SAMPLE_EXPERIMENTAL (beats technical on top-10 return with positive IC)", metrics


def _us_catalyst(snapshot, earnings, features):
    """A recent earnings report (POST_EARNINGS_*) with a sourced surprise, confirmed by price and volume."""
    if earnings not in ("POST_EARNINGS_DAY_1", "POST_EARNINGS_DAY_2_3"):
        return None, []
    surprise = (snapshot or {}).get("eps_surprise_pct")
    if surprise is None:
        return None, []
    direction = "POSITIVE" if surprise > 0 else "NEGATIVE"
    event = [{"event_type": "EARNINGS_BEAT" if surprise > 0 else "EARNINGS_MISS", "direction": direction,
              "detail": f"EPS surprise {surprise:.1f}% (scanner snapshot)"}]
    return fusion.catalyst_score(event, rvol=features.get("rvol"),
                                 sector_accel=features.get("sector_turnover_accel")), event


def score_session(ds, *, market, session, forecasts, events_by_isin, cache, now_iso, records=None):
    config = fusion.MARKETS[market]
    records = records or {}
    by_session = ds.by_session()
    k = ds.session_index[session]
    members = by_session.get(k, [])
    regime, _ = fusion.regime_series(ds, by_session)
    quant = fusion.QuantEdge(config["quant_groups"]).fit(ds, by_session, [s for s in by_session if s < k], None)
    tech = [fusion.technical_score(lambda name, i=i: ds.f[name][i], market) for i in members]
    quant_scores = quant.scores(ds, members)
    sector = fusion.sector_scores(ds, members)
    champion = [((forecasts.get(ds.ticker(i)) or {}).get("FC-BASE-v1") or {}).get("3") or {} for i in members]
    ridge = [((forecasts.get(ds.ticker(i)) or {}).get("FC-RIDGE-v1") or {}).get("3") or {} for i in members]
    forecast_pct = fusion.percentiles([c.get("expected_return") if c.get("status") == "OK" else None for c in champion])
    ridge_pct = fusion.percentiles([c.get("expected_return") if c.get("status") == "OK" else None for c in ridge])
    snapshots = [(records.get(ds.ticker(i)) or {}).get("snapshot") or {} for i in members]
    fundamentals = fusion.fundamentals_scores(snapshots) if market == "US" else [None] * len(members)
    arm, arm_status, arm_metrics = best_arm(cache, market)
    learned = ((cache or {}).get("evaluation") or {}).get("df5_latest_weights")
    weights = fusion.arm_weights(arm, learned, market)
    window_start = (date.fromisoformat(session) - timedelta(days=7)).isoformat()
    symbols = {}
    for pos, i in enumerate(members):
        ticker = ds.ticker(i)
        features = ds.features(i)
        record = records.get(ticker) or {}
        isin = ds.isins[ds.row_ticker[i]]
        earnings = "NOT_APPLICABLE"
        if market == "US":
            earnings = fusion.earnings_state(session=session, next_release=snapshots[pos].get("earnings_next"),
                                             last_release=snapshots[pos].get("earnings_last"))
            catalyst, recent = _us_catalyst(snapshots[pos], earnings, features)
        else:
            recent = [e for e in events_by_isin.get(isin, []) if (e.get("published_at") or "")[:10] >= window_start
                      and max(e.get("published_at") or "", e.get("first_seen") or "") <= now_iso]
            catalyst = fusion.catalyst_score(recent, rvol=features.get("rvol"),
                                             sector_accel=features.get("sector_turnover_accel"))
        scores = {"technical": tech[pos], "quant": quant_scores[pos], "sector": sector[pos],
                  "forecast": forecast_pct[pos], "catalyst": catalyst, "regime": regime.get(k)}
        if market == "US":
            scores["fundamentals"] = fundamentals[pos]
        by_arm = {a: fusion.opportunity(scores, fusion.arm_weights(a, learned, market))[0] for a in config["arms"]}
        value, share = fusion.opportunity(scores, weights)
        disagreement = (abs(forecast_pct[pos] - ridge_pct[pos])
                        if forecast_pct[pos] is not None and ridge_pct[pos] is not None else None)
        item = {
            "ticker": ticker, "market": market, "sector": ds.sectors[ds.row_ticker[i]],
            "industry": snapshots[pos].get("industry"), "technical_score": tech[pos],
            "components": {c: (round(v, 1) if v is not None else None) for c, v in scores.items()},
            "opportunity": value, "opportunity_by_arm": by_arm, "arm": arm, "available_weight_share": share,
            "confidence": fusion.confidence(available_share=share, arm_metrics=arm_metrics,
                                            forecast_support=champion[pos].get("support"), disagreement=disagreement,
                                            stale=record.get("freshness") not in (None, "CURRENT")),
            "evidence_quality": fusion.evidence_quality(scores, fresh=record.get("freshness") in (None, "CURRENT"),
                                                        sector_known=bool(ds.sectors[ds.row_ticker[i]]),
                                                        forecast_status=champion[pos].get("status", "UNKNOWN"),
                                                        calibration_status="SEE_PERFORMANCE"),
            "explanation": fusion.explain(scores),
            "forecast": {"expected_r1": (((forecasts.get(ticker) or {}).get("FC-BASE-v1") or {}).get("1") or {})
                         .get("expected_return"), "expected_r3": champion[pos].get("expected_return"),
                         "expected_mae3": champion[pos].get("expected_mae"), "p": champion[pos].get("p"),
                         "p_status": champion[pos].get("p_status")},
            "catalysts": [{k2: e.get(k2) for k2 in ("event_type", "published_at", "heading", "direction", "detail")}
                          for e in recent[:3]],
            "liquid": (features.get("turnover20") or 0) >= LIQUIDITY_FLOOR[market],
            "risk": {"volatility20": features.get("vol20"), "high52_dist": features.get("high52_dist"),
                     "atr_pct": features.get("atr_pct")}}
        if market == "US":
            item["earnings_state"] = earnings
            item["gap_state"] = fusion.gap_class(features.get("gap1"), features.get("intraday1"))
            item["options_context"] = "UNAVAILABLE"
            item["gates"] = fusion.us_gates(record=record, features=features, earnings=earnings,
                                            freshness=record.get("freshness", "UNKNOWN"))
        symbols[ticker] = item
    if market == "US":
        _apply_us_actions(symbols, records)
    ranked = sorted((s for s in symbols.values() if s["liquid"] and s["opportunity"] is not None),
                    key=lambda s: (-s["opportunity"], -(s["technical_score"] or 0), s["ticker"]))
    for index, item in enumerate(ranked, 1):
        item["opportunity_rank"] = index
    return {"market": market, "strategy": config["strategy"], "config": config["config"], "session": session,
            "arm": arm, "arm_status": arm_status, "arm_metrics": arm_metrics, "weights": weights,
            "cache_generated_at": (cache or {}).get("generated_at"), "quant_group_ic": quant.ic,
            "options_context": "UNAVAILABLE" if market == "US" else "NOT_APPLICABLE",
            "symbols": symbols, "top": [s["ticker"] for s in ranked[:25]]}


def _apply_us_actions(symbols, records):
    """US final action: gates first; at most ``max_per_sector`` actionable names per sector (by Opportunity Score)."""
    per_sector = {}
    for item in sorted(symbols.values(), key=lambda s: (-(s["opportunity"] or -1), s["ticker"])):
        technical_class = (records.get(item["ticker"]) or {}).get("classification")
        gates = item["gates"]
        eligibility, reasons = fusion.us_eligibility({k: v for k, v in gates.items() if k != "sector_concentration"})
        if eligibility == "ELIGIBLE" and technical_class in ("STRONG_CANDIDATE", "CANDIDATE"):
            count = per_sector.get(item["sector"], 0)
            if count >= fusion.US_GATES["max_per_sector"]:
                gates["sector_concentration"] = "FAIL"
                eligibility, reasons = "BLOCKED", ["sector_concentration"]
            else:
                gates["sector_concentration"] = "PASS"
                per_sector[item["sector"]] = count + 1
        else:
            gates["sector_concentration"] = "NOT_EVALUATED"
        item["trade_eligibility"], item["gate_failures"] = eligibility, reasons
        item["technical_class"] = technical_class
        item["final_action"] = fusion.final_action(opportunity_score=item["opportunity"], eligibility=eligibility,
                                                   technical_class=technical_class, gate_action=None)
