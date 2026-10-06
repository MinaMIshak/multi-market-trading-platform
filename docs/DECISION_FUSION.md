# Decision Fusion: EGX and US quant opportunity engines (research)

RESEARCH / PAPER / SHADOW ONLY. LIVE_MONEY=DISABLED. One shared infrastructure, separate market models.

## Concepts kept separate

These are never merged:
- Technical Score;
- Technical Class;
- Trade Eligibility (hard gates);
- Opportunity Score;
- Confidence;
- Evidence Quality;
- Final Paper/Shadow Action.

A high Opportunity Score never overrides a failed gate (`fusion.final_action`). A stale, unknown or failed
gate always gives BLOCKED, WATCH or NO_TRADE, never PAPER_ENTRY.

## Architecture

```
data quality → technical → quant edge → sector/flow → catalyst → regime → forecast
   → Opportunity Score (0–100) → confidence + evidence quality → hard gates → final action
```

| Shared (`app/learning/`) | Separate per market |
|---|---|
| `dataset.py` (LEARN-DATA-v2, columnar), `walkforward.py`, `fusion_eval.py` metrics, `registry.py` | datasets, fitted models, quant edges, learned weights, calibration, state directories (`learning/egx`, `learning/us`) |

There is no blended EGX+US model. Each dataset is built only from that market's own series, so labels
can never cross markets (tests: `test_markets_are_isolated_datasets_and_state`).

## Components (0–100, point in time)

| Component | Definition | EGX | US |
|---|---|---|---|
| technical | the market's ranking score rebuilt exactly from features (EGX-RANK-v1 or US-RANK-v1 thresholds; CPCI parity test 82.18) | ✓ | ✓ |
| quant | training-only factor-group quintile edges; groups whose training rank IC is ≤ 0 get no weight | momentum, participation, position, risk | momentum, reversal, volume surprise, position, risk, gap |
| sector | percentile mean of sector 5-session return, breadth and turnover acceleration (market-flow proxy) | ✓ | ✓ (industry shown; industry-level aggregation is research) |
| fundamentals | percentile mean of sourced snapshot fields (EPS and revenue surprise, revenue growth, net margin, ROE; needs ≥ 3 fields) | – | live only; UNKNOWN in backtests |
| forecast | within-session percentile of the forecast champion's expected 3-session return | ✓ | ✓ |
| catalyst | sourced events counted only with confirmation (RVOL ≥ 1.5 or sector turnover acceleration > 1); narrative alone is 50 | EGX disclosures and results | recent earnings with surprise (scanner) |
| regime | breadth percentile vs prior sessions (constant within a session, so it never changes the ranking) | ✓ | ✓ |

Missing components are excluded and the weights renormalised; they are never scored as zero. Evidence
Quality lists each component (VERIFIED, AVAILABLE, UNKNOWN, STALE, PARTIAL or INSUFFICIENT_SAMPLE).

## Arms and weights (versioned; an older arm is never edited)

**EGX, `DF-CFG-1`** (effective 2026-10-06). Baseline weights: technical 25, quant 25, sector 20,
forecast 15, catalyst 8, regime 7.

| Arm | Components |
|---|---|
| DF0 | technical |
| DF1 | + quant |
| DF2 | + sector |
| DF3 | + forecast |
| DF4 | + catalyst + regime |
| DF5 | learned weights |

**US, `US-DF-CFG-1`** (effective 2026-10-06). Baseline weights: technical 20, quant 20, fundamentals
20, sector 15, forecast 15, catalyst 5, regime 5.

| Arm | Components |
|---|---|
| US-DF0 | technical |
| US-DF1 | + quant |
| US-DF2 | + sector |
| US-DF3 | + fundamentals |
| US-DF4 | + forecast |
| US-DF5 | + catalyst + regime |
| US-DF6 | learned weights |

**Learned weights (DF5 / US-DF6), the redundancy control:**
- non-negative ridge regression of component scores on the forward 3-session return;
- fitted only on earlier folds' out-of-sample scores whose labels matured before the fold;
- water-filling cap of 35% per component;
- equal weights when the cap is infeasible;
- the DF4 / US-DF5 weights until 500 such rows exist.

Correlations between components are reported. The largest was technical~quant at 0.33 (EGX).

## Confidence

| Level | Rule |
|---|---|
| INSUFFICIENT_SAMPLE | no validated arm |
| HIGH | out-of-sample rank IC > 0.05, ≥ 85% of weight available, forecast support ≥ 1000, and model disagreement < 25 percentile points |
| MEDIUM | IC > 0.02 and ≥ 70% of weight available |
| LOW | otherwise, or stale data |

A score of 91 with LOW confidence is possible by design.

## Hard gates

- **EGX:** the existing V2A–V2D gates (liquidity, resistance, entry, chase, expiry, portfolio risk),
  unchanged.
- **US (`fusion.us_gates`):**
  - stale data;
  - minimum liquidity ($20M per day);
  - earnings event risk (EARNINGS_IN_0_1_DAYS blocks);
  - volatility limit (20-session σ ≤ 6%);
  - gap chase (last gap ≥ 5% that held);
  - stop feasibility (plan risk ≤ 10%);
  - sector concentration (at most 3 actionable names per sector);
  - spread: NOT_MEASURABLE;
  - portfolio risk: NOT_EVALUATED.

## US layers

| Layer | What it does |
|---|---|
| Earnings | NO_NEAR_EARNINGS / EARNINGS_IN_0_1_DAYS / EARNINGS_IN_2_5_DAYS / POST_EARNINGS_DAY_1 / POST_EARNINGS_DAY_2_3 / UNKNOWN, from the scanner's release dates on the NYSE calendar |
| Gap | GAP_UP_CONTINUATION / GAP_UP_FADE / GAP_DOWN_REVERSAL / GAP_DOWN_CONTINUATION (±2%); conditional outcomes in the gap research (descriptive) |
| Fundamentals | TradingView scanner snapshot fields, recorded per US run (retrieval-time evidence; no licence; no history before 2026-10). SEC stays BLOCKED (NO_OPERATOR_CONTACT_EMAIL) |
| Benchmarks | SPY and QQQ daily bars (`app/us/benchmarks.py`), used for the Top-10 excess return; never ranking inputs |
| Segmentation | research only: arms evaluated within dollar-turnover tiers (a cap proxy). No segment model is deployed until samples support it |
| Options | OPTIONS_CONTEXT = UNAVAILABLE |

## Validation and first results

Walk-forward per market (`app/learning/fusion_eval.py`):
- calendar-month folds; quant edges and the forecast champion are fitted on rows before the fold;
- metrics on liquid symbols: rank IC; recall of the actual Top-10/20 in the predicted Top-10/20;
  Precision@K; Top-10 next-session and 3-session returns vs the benchmark; Top-10 MAE; profit factor;
  drawdown of the cumulative Top-10 next-session return;
- splits by regime (breadth above/below its median) and, for US, by turnover tier;
- an incremental view (each arm vs the previous one).

**EGX, 2026-10-06 run:** 538 sessions, 118,448 out-of-sample rows, 2024-07-16 → 2026-10-05.

| Arm | Rank IC r3 | Recall top-10@10 | Top-10 r3 | Universe r3 | PF r3 | Max DD (cum r1) |
|---|---|---|---|---|---|---|
| DF0 | −0.036 | 0.144 | +1.38% | +0.66% | 1.65 | −0.18 |
| DF1 | −0.048 | 0.151 | +1.34% | +0.66% | 1.60 | −0.15 |
| DF2 | −0.041 | 0.148 | +1.33% | +0.66% | 1.61 | −0.30 |
| DF3 | −0.036 | 0.149 | +1.35% | +0.66% | 1.62 | −0.19 |
| DF4 | −0.036 | 0.148 | +1.35% | +0.66% | 1.62 | −0.19 |
| DF5 | −0.022 | 0.115 | +0.98% | +0.66% | 1.50 | −0.26 |

**Reading the EGX results:**
- The full-cross-section rank IC is negative for every arm: short-term mean reversion.
- The top-ranked names still beat the universe (DF0 top-10 +1.38% vs +0.66% over 3 sessions).
- No fusion arm beats DF0 on top-10 return. DF1 adds marginal leader recall and a smaller drawdown.
- **The technical arm (DF0) stays the champion.** Fusion is EXPERIMENTAL.
- Factor research: breakout with sector strength has a mean 3-session return of +2.19% (n = 2,512),
  vs +1.30% for a breakout without sector strength (descriptive, in-sample).

**US, 2026-10-06 run:** 177 sessions, 90,378 out-of-sample rows, 2026-01-16 → 2026-10-05. Benchmarks
are SPY and QQQ.

| Arm | Rank IC r3 | Recall top-10@10 | Top-10 r3 | Universe r3 | Excess vs SPY | PF r3 | Max DD |
|---|---|---|---|---|---|---|---|
| US-DF0 | −0.000 | 0.050 | +0.53% | +0.30% | +0.32% | 1.23 | −0.27 |
| US-DF1 | +0.004 | 0.061 | +0.70% | +0.30% | +0.49% | 1.29 | −0.32 |
| US-DF2 | −0.002 | 0.054 | +0.99% | +0.30% | +0.78% | 1.46 | −0.24 |
| US-DF3 | −0.002 | 0.054 | +0.99% | +0.30% | +0.78% | 1.46 | −0.24 |
| US-DF4 | −0.003 | 0.058 | +0.81% | +0.30% | +0.60% | 1.35 | −0.40 |
| US-DF5 | −0.003 | 0.057 | +0.79% | +0.30% | +0.59% | 1.34 | −0.41 |
| US-DF6 | −0.002 | 0.053 | +1.24% | +0.30% | +1.04% | 1.57 | −0.23 |

**Reading the US results:**
- The rank IC is about zero for every arm. The value is concentrated in the top of the list.
- US-DF6 (learned weights; on the latest fold they concentrated on quant, which made the 35% cap
  infeasible, so equal weights over one component) has the best Top-10 return and profit factor.
- US-DF3 equals US-DF2 in backtests because fundamentals have no history (UNKNOWN until snapshots
  accumulate).
- This is a single nine-month period, so **US-DF0 stays the champion** and US-DF6 is EXPERIMENTAL.

Gap research (descriptive): after GAP_DOWN_CONTINUATION, the mean next-3-session return is +0.92%
(n = 3,523), higher with RVOL ≥ 1.5 (+1.16%, n = 904). This is a mean-reversion tendency, not a rule.

**Forecast walk-forward (3-session horizon):**

| Market | Model | Rank IC | Top-10 return vs benchmark | Brier +5% vs base rate |
|---|---|---|---|---|
| EGX | FC-BASE-v1 | 0.003 | +0.77% vs +0.66% | 0.1152 vs 0.1160 |
| EGX | FC-RIDGE-v1 | 0.012 | +1.49% vs +0.66% | 0.1163 vs 0.1160 |
| US | FC-BASE-v1 | −0.006 | +0.48% vs +0.30% | 0.1152 vs 0.1153 |
| US | FC-RIDGE-v1 | −0.007 | +1.59% vs +0.30% | 0.1144 vs 0.1153 |

+20% probabilities equal the base rate in both markets (no skill). Probabilities are shown with
their status and must not be read as edges.

## Promotion policy

There is no auto-promotion. The checkpoints are 20 (early), 50 (meaningful) and 100+ (stronger)
matured sessions. Promotion requires all of the following, plus an operator decision:
- better out-of-sample ranking and Top-K return;
- acceptable downside;
- robust calibration;
- results across more than one regime.

Weight changes are new config versions.

## Operations and resource safety

| Job | When | Peak memory (measured) |
|---|---|---|
| EGX learning (`learning_egx`) | EGX chain, Sun–Thu 08:30 Cairo | about 157 MB |
| US learning (`learning_us`) | US chain, Tue–Sat 06:15 Cairo | about 152 MB |
| Weekly heavy (`tools/learning_weekly.sh`) | Saturday 10:00 Cairo, strictly sequential: EGX forecast WF → EGX fusion WF → US forecast WF → US fusion WF | EGX WF 176 MB / 4 min 16 s; EGX fusion 183 MB / 1 min 42 s; US WF 167 MB / 1 min 54 s; US fusion 159 MB / 1 min 2 s |

Each daily step scores the latest session, freezes the forecast and fusion snapshots, and scores
matured frozen snapshots (live out-of-sample evidence).

All learning jobs share `learning.lock`, so two heavy learning jobs never run at once. Production
models are not retrained daily; the walk-forward caches are refreshed weekly.
