# Session learning and short-horizon upside forecasts

RESEARCH / PAPER / SHADOW ONLY. LIVE_MONEY=DISABLED. A forecast is not a
candidate, not a fill and not a guarantee.

The forecasts sit beside the existing labels and never replace them:
- Technical class (EGX-RANK-v1, the unchanged strategy champion);
- Trade eligibility (EGX-EXP-v1 V2A–V2D, unchanged);
- the Paper/Shadow action;
- Upside forecast;
- Forecast confidence.

## Phase gate

Learning datasets are built only after the Phase 1 session-integrity fixes
(`docs/EGX_SESSION_INTEGRITY.md`) and only from validated, session-dated
primary bars:
- EGX: TradingView artifacts through the approved validated reader;
- US: the immutable canonical artifacts from `app.us_run`.

The official market-watch source is never a label or feature price.

## Dataset (`app/learning/dataset.py`, LEARN-DATA-v2 columnar / FEAT-v1)

**Storage (v2):** columnar typed arrays, with NaN or -1 for missing and never 0. The v1 dict-per-row
design peaked at 880 MB for EGX on this 1.9 GB host. v2 peaks at about 157 MB for the EGX daily run
and about 176 MB for the EGX walk-forward. The rules are unchanged.

Added extras: the session gap and the intraday move. They are used by US research and gates; they are
not FEAT-v1 model features.

**Observed sessions:** market-wide dates on which at least max(10, 30%)
symbols have a bar. Weekends and sparse dates are never sessions.

**Labels per (symbol, session t), for h ∈ {1, 2, 3, 5}:**
- the return to the close h sessions later;
- MFE and MAE (max high and min low over the h sessions vs the close at t);
- threshold flags at +3, +5, +10, +15 and +20% (and the same flags for MFE);
- `label_end`, the session on which the label matured.

A label needs the symbol's bars on each of the next h observed sessions.
Otherwise it is `MISSING_SESSION` (never forward-filled); a label still in the
future is `NOT_MATURED`.

**Features:** from bars ≤ t only, with at least 60 sessions of history
(otherwise unavailable).

| Group | Features |
|---|---|
| Returns | 1/5/20/60-session returns |
| Trend | EMA20 gap, EMA stack |
| Volatility | ATR%, 20-session volatility |
| Volume and turnover | relative volume, turnover and its 5/20 acceleration, log 20-session turnover |
| Price structure | breakout distance, distance from the 52-week high |
| Cross-sectional | relative strength (r20 minus the same-session median) |
| Sector | sector 5-session return, breadth, turnover acceleration (same session) |

**Survivorship:** the universe is the symbols with a current admitted series,
so delisted names are absent. Historical metrics carry this caveat.

**EGX sectors:** taken from the newest official market-watch rows by ISIN, as a
static attribute only.

## Models (`app/learning/models.py`, pure Python, no LLM)

| Version | Role | Method |
|---|---|---|
| FC-BASE-v1 | forecast champion | Hierarchical bucket frequencies: momentum tercile × RVOL bucket × EMA stack, falling back to the momentum tercile, then the global rate. Laplace-smoothed probabilities, calibrated by construction. Winsorised expected return and MAE. Minimum 50 rows per bucket |
| FC-BASE-SECTOR-v1 | challenger | The same, plus a sector-breadth tercile |
| FC-RIDGE-v1 | challenger | Ridge regression (λ = 10) on standardised features. Probabilities from the empirical training-residual distribution. Top feature contributions recorded |

A threshold probability with fewer than 30 training events is labelled
`LOW_EVENT_COUNT`. A forecast without enough support is `INSUFFICIENT_SAMPLE`.

**Top upside ranking:**
- liquid symbols only: 20-session average turnover ≥ 1M EGP, or ≥ $20M for US;
- ordered by the champion's expected 3-session return;
- the champion is coarse (bucket ties), so ties are broken by the continuous
  FC-RIDGE-v1 estimate, then by the champion's P(+5%).

**Confidence:**
- UNVALIDATED until walk-forward evidence exists;
- then HIGH (support ≥ 1000 and walk-forward Spearman > 0.03), MEDIUM (support
  ≥ 200 with skill) or LOW.

## Validation (`app/learning/walkforward.py`)

- Chronological, never shuffled: calendar-month folds, an expanding window,
  and at least 120 training sessions.
- A fold starting at session S trains only on labels with `label_end < S`.
- Metrics per model and horizon:
  - error: MAE, median absolute error, directional accuracy;
  - ranking: mean per-session Spearman; Precision@5/10/20; recall of the actual
    Top-10/Top-20 gainers in the predicted Top-10/Top-20; mean and median
    realised return of the predicted Top-K vs the equal-weight universe
    benchmark; false-positive share;
  - calibration: Brier score vs the climatology (base-rate) Brier, buckets and
    event counts for every threshold.
- Run weekly: `tools/learning_weekly.sh`, Saturday 10:00 Cairo, a non-session
  day. The four heavy jobs run strictly in sequence under `learning.lock`.
  Results are cached in `walkforward.json` and `fusion_walkforward.json`, and
  no model is promoted automatically.
- Daily: `learning_egx` runs in the EGX chain and `learning_us` in the US chain.
  Each processes only its own market, and both share `learning.lock`.

## Daily operations (`app/learning/daily.py`)

The nightly steps `learning_egx` (EGX chain) and `learning_us` (US chain) each run one market:
1. Build the dataset and fit every model on matured labels.
2. Forecast every symbol at the latest session.
3. Freeze `forecasts/<session>.json` (written once) with the build revision,
   model versions, feature schema, training window, generation time and
   forecast cutoff.
4. Score earlier frozen forecasts as their horizons mature. These live
   out-of-sample results are kept separate from the backtest.
5. **Last session's winners:** the Top-10/20 gainers, losers, top turnover and
   top relative volume. Each winner shows what was known before the move:
   - features at the prior session;
   - the archived V1 ranking (rank, class, score), from `archive/ranking-<session>.json`,
     seeded from the published bundles;
   - the frozen forecast (rank, expected return, P(+5/+10/+20%));
   - caught or missed, with machine-readable miss reasons;
   - events with publication and first-seen times at or before the forecast
     cutoff.
6. **Hit analysis:** the prior forecast Top-10 with realised returns and the
   prior state.
7. **Sector analytics:** return, breadth, advancing/declining, turnover and
   share, acceleration, median RVOL, breakouts, high-RVOL names and volatility,
   relative to the equal-weight universe. These are called market-flow
   proxies, never institutional flow.
8. **Events (`app/learning/events.py`, EVENT-TAX-v1):**
   - EGX disclosures (DIVIDEND, CAPITAL_INCREASE, ACQUISITION, MERGER,
     GOVERNMENT_CONTRACT, REGULATORY_CHANGE, MAJOR_COMPANY_DISCLOSURE),
     mapped by ISIN;
   - EGX results (EARNINGS_BEAT / MISS vs the disclosed comparative, not vs
     expectations);
   - macro events from context regimes (BRENT_MOVE, GOLD_MOVE, CURRENCY_EVENT,
     INTEREST_RATE_HIKE / CUT, RED_SEA_DISRUPTION, ENERGY_PRICE_SHOCK), with
     sector mappings recorded as explicit hypotheses.

   Events are not model inputs until enough event history exists for an
   out-of-sample evaluation.

Miss reasons: OUT_OF_UNIVERSE, LIQUIDITY_GATE, MOMENTUM_THRESHOLD,
BREAKOUT_THRESHOLD, STALE_DATA, FEATURE_UNAVAILABLE,
MISSED_WINNER_RANK_TOO_LOW, MODEL_UNDERESTIMATION,
INSUFFICIENT_SOURCE_EVIDENCE (no ranking or forecast existed for the prior
session) and NO_CATALYST.

## UI

| Page | Additions |
|---|---|
| TODAY (EGX, US) | "Top upside forecasts — next 1–3 sessions", with technical class and trade eligibility beside each forecast; "Last session's top winners — what did we know beforehand?" |
| PERFORMANCE | Forecast performance: walk-forward table, calibration vs base rate, live frozen-forecast scoring with INSUFFICIENT_SAMPLE labels |
| RESEARCH | Sector rotation and market-flow proxies per market; events and themes |
| SYSTEM | Learning audit: champion, challengers, data and feature versions, training window, last outcome session, frozen files, walk-forward time, label counts |

## Not done (truthfully)

- No event or news feature in the models yet; the event history is too short.
- No investor-category flow data, so there is no institutional-flow metric.
- US SEC filings and fundamentals are BLOCKED (no operator contact).
- The CBE policy rate is BLOCKED.
- No model promotion: the forecast champion stays until out-of-sample evidence
  supports a change.
