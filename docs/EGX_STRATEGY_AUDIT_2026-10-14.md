# EGX strategy performance and data-integrity audit (2026-10-14)

Scope: EGX-RANK-v1 champion (V1) and the EGX-EXP-v1 challenger arms V2A–V2D, run on
release `61b97f0`. The audit is diagnostic only. No strategy, threshold, weight, stop,
target or arm semantic was changed. Nothing was promoted, no historical outcome was
rewritten, and LIVE_MONEY stays DISABLED. The read-only scripts are in
`tools/egx_strategy_audit/`; each prints the evidence quoted below.

Frozen versions:

| Item | Version |
|---|---|
| Release | 61b97f05eb8984d03060e4acb0392c9ee5845947 |
| Ranking | EGX-RANK-v1 |
| Experiment | EGX-EXP-v1 / EGX-EXP-CFG-1 |
| Learning | LEARN-DATA-v2 / FEAT-v1 |
| Forecast champion | FC-BASE-v1, with challengers FC-BASE-SECTOR-v1 and FC-RIDGE-v1 |
| Decision Fusion | DF-CFG-1 (DF0–DF5) |

Ledger: 158 candidates from sessions 2026-10-01 to 2026-10-13. Of these, 74 are closed,
69 open, 13 pending entry and 2 did not fill (gap down).

## A. Executive summary

- **No implementation bug produced the losses.**
  - The fills, stops, targets, breakeven moves and costs were re-derived from the stored
    bars for all 74 closed trades and for the case cards below.
  - Every exit reconciles with the stored bars.
  - On all 69 stop days the primary low agrees with the official, session-aligned
    market-watch low within 2%.
  - No candidate was retrospective: every one was generated before its entry session
    opened.
- **The results are genuinely poor.** Mechanism:
  1. The candidates were generated during a sharp short rebound (10-01 +3.7%,
     10-04 +2.3%, equal-weight).
  2. The broad sell-off that followed stopped them out: 10-11 −2.2%, 10-12 −3.5%,
     10-13 −2.3%, with breadth of 8–15%.
  3. 66 of 70 stop-outs happened on 10-11 to 10-13 (6, 29 and 31).
- **The headline overstates the number of independent trades.**
  - V1 keeps re-recording a ticker that is already held. 66 of 74 closed lifecycles
    overlapped another lifecycle of the same ticker.
  - The 74 closed results are only 43 distinct (ticker, exit day) events across 31
    tickers.
  - With one position per ticker, V1 has 28 closed trades, 3 wins and −0.818R
    expectancy.
- **The headline mixes cost bases.** This is a reporting issue, fixed as observability:
  - The ranking report's `expectancy_r` (−0.885) and `max_drawdown_r` (−65.5R) are
    **gross** R, before costs.
  - Win rate and the average win/loss % in the same block are net.
  - The net figures are −0.940R and −69.57R. They are now reported beside the gross
    values, with an explicit `r_basis` label.
- **The challenger gates removed trades but did not add edge in this regime.** The trades
  they let through were as bad as the trades they blocked.
- **No challenger or Decision Fusion arm qualifies for promotion.**

## B. V1 (champion, net R after the V1 cost model)

| Metric | Value |
|---|---|
| Closed / wins / losses | 74 / 4 / 70 |
| Win rate | 5.4% |
| Expectancy (net) | −0.940R; gross headline −0.885R |
| Median R | −1.048 |
| Cumulative R / max drawdown | −69.57R / −69.57R |
| Profit factor | 0.057 |
| T1 / T2 / stop hit rate | 5.4% / 1.4% / 94.6% |
| Average MFE / MAE | +0.44R / −1.24R |
| Average holding | 3.74 sessions (median 4) |
| Average win / loss | +9.34% / −9.88% |
| Stop distance | median −9.46%, range −3.6% to −16% |
| One position per ticker | 45 entries, 28 closed, 3 wins, −0.818R, cumulative −22.9R |

## C. V2 arms

| Arm | Eligible | Closed | Wins | Expectancy | Profit factor | One position per ticker |
|---|---|---|---|---|---|---|
| V2A liquidity | 158 | 74 | 4 | −0.940R | 0.057 | 28 closed, −0.818R |
| V2B resistance ≥ 1.8R | 41 | 19 | 1 | −0.958R | 0.037 | 12 closed, −0.906R |
| V2C entry validity | 41 | 19 | 1 | −0.958R | 0.037 | 12 closed, −0.906R |
| V2D sizing + 3% cap | 12 entered of 41 | 5 | 0 | −1.050R | 0.000 | 4 closed, −1.048R |

- **V2A equals V1.** Every candidate passed the 5% turnover gate.
- **V2C equals V2B.** This is a data-semantics limitation (see H): EGX's daily open
  equals the previous close in 81% of 2026 bars, so the gap/chase tests in V2C almost
  never trigger.
- **Sample sizes are too small for promotion:** 19 and 5 closed trades, all in one regime.

## D. Gate attribution (counterfactual against V1's closed outcomes)

| Gate | Blocked | Of which V1-closed | V1 losers avoided | V1 winners excluded | Average V1 R of blocked |
|---|---|---|---|---|---|
| RESISTANCE_RR_FAIL | 117 | 55 | 52 | 3 | −0.934 |
| PORTFOLIO_RISK_CAP | 29 | 14 | 13 | 1 | −0.925 |

- **Average V1 R:** blocked trades averaged about −0.93R and passed trades about −0.96R.
  The gate does not separate good trades from bad ones in this sample.
- **Resistance follow-through:** of 55 failed cases, 22 reached within 1% of the
  resistance level and 14 broke above it.
- **Winners excluded by the resistance gate:**
  - AJWA 10-05 was blocked with 0.10R of room to 192.0 and then made T2 (+2.11R).
  - RUBX 10-01 and 10-04 were blocked with 1.27R and 1.09R and made T1, then exited at
    breakeven.
- **Effect of the 1.8R threshold: INSUFFICIENT EVIDENCE.** It was not changed.

## E. Score quality

Technical score bands (closed V1, net R):

| Band | Count | Expectancy |
|---|---|---|
| < 65 | 30 | −0.995 |
| 65–75 | 27 | −0.924 |
| 75–85 | 8 | −0.658 |
| ≥ 85 | 9 | −1.056 |

- **Not monotonic.** STRONG_CANDIDATE (n=10, −0.748) vs CANDIDATE (n=64, −0.970).
- **Walk-forward over about 540 sessions** (Decision Fusion report, DF0 = technical):
  - Rank IC is −0.035.
  - Yet the top 10 returns +1.37% over 3 sessions against +0.64% for the equal-weight
    universe.
  - The score separates the very top of the list but does not rank the whole universe.
- **Live frozen fusion snapshots: INSUFFICIENT SAMPLE.** There are 5 transitions, all in
  the sell-off:
  - DF0 is the best live arm, with IC +0.081 and top-10 next-session return −0.41%
    against −1.77% for the benchmark.
  - DF5 is the worst, with IC −0.035.
- **Cohorts: INSUFFICIENT EVIDENCE (4 winners).** Winners had higher relative volume
  (2.94 vs 1.95) and wider stops (11.3% vs 9.5%).

## F. Missed winners

Daily top-winner recall, scored against the ranking archived the session before, over 7
sessions:

| Metric | Value |
|---|---|
| Top-10 in our top 10 | 0.143 |
| Top-10 in our top 20 | 0.214 |
| Top-20 in our top 20 | 0.179 |
| Random baseline | ≈ 0.045 |

Miss reasons (mean next-session return of the missed winner):

| Reason | Count | Mean return | Note |
|---|---|---|---|
| TECH_SCORE_TOO_LOW (DOWNTREND / LOW_SCORE) | 29 | +4.78% | |
| RANK_TOO_LOW | 9 | +5.01% | |
| LIQUIDITY_GATE | 9 | +4.40% | |
| STALE_DATA | 8 | +12.45% | All 8 come from 10-04 against the bootstrap 10-01 ranking; a one-time artifact of the first run that does not recur. |

- **The biggest daily winners are mostly oversold rebounds.** Examples:
  - DAPH +10.8% (mom20 −25%, DOWNTREND);
  - BIGP +9.8% (mom20 −15%, DOWNTREND);
  - SIPC and HBCO (LOW_SCORE).
- **Others were ranked near the top:**
  - ARAB at rank 3;
  - AJWA at rank 4;
  - MMAT at rank 5;
  - ADRI at rank 5.
- A trend-following score excludes rebounds by design (WORKING AS DESIGNED). A rebound
  model is a NEW CHALLENGER HYPOTHESIS.

## G. Lifecycle integrity (reproducible case cards, `audit_cases.py`)

Every card reconciles with the stored OHLC bars. Each entry filled at the entry-session
open inside the zone; each stop fired on the first bar whose low crossed it; the stop
moved to breakeven after T1; and costs were about −0.05R per stop.

| Case | Ticker / session | Outcome | Gates |
|---|---|---|---|
| Winner | AJWA 10-05 | Filled 190.23 on 10-06. T1 201.29 and T2 212.36 both hit on 10-12 (high 212.99): **+2.11R** net. | Blocked by V2B (0.10R room). |
| Winner | MBSC 10-04 | T1 hit on the entry bar (high 414.99 ≥ 407.51). Breakeven exit on 10-12. | Passed V2B; blocked by the V2D cap. |
| Winner | RUBX 10-01 / 10-04 | T1 hit on 10-06. Breakeven exit on 10-13. | Blocked by V2B. |
| Loser | SNFC 10-07 | Stop 3.6% away (11.22) hit on 10-12 (low 11.03): −1.13R. | Blocked by V2B (0.02R room). |
| Loser | AIHC 10-07 | Stop at 0.76 hit on 10-13 (low 0.736). | |
| V2D pass, closed | BIOC 10-01 (room 6.24R) | Stopped on 10-11. | |
| V2D pass, closed | INEG 10-04 (no resistance above) | Stopped on 10-06. | |
| Portfolio-cap block | ALCN 10-04 | V1 lost −1.07R. | |
| Portfolio-cap block | BIOC 10-04 | V1 lost −1.04R. | Duplicate of the open BIOC 10-01 position. |

Known modelling choices (WORKING AS DESIGNED, documented in `system_candidates.py`):

- **Stop-first:** when the stop and a target trade in the same bar, the stop is assumed
  to have traded first.
- **No breakeven stop on the T1 bar:** after T1 the breakeven stop is not checked on that
  same bar. A breakeven exit is priced the same either way, so this only matters if T2
  is hit later.

## H. Data integrity

- **Learning labels (`audit_labels.py`):** 508,687 labels checked over 130,286 rows
  (2024-01-17 to 2026-10-13). All checks pass:
  - no weekend sessions;
  - 2026-10-08 is not a session;
  - no duplicate (ticker, session) rows;
  - each label ends exactly h observed sessions later;
  - each label return equals the close ratio;
  - no label spans a missing bar (no forward-fill);
  - the 10-07 h1 label matures on 10-11;
  - every frozen forecast was made after its feature session and before its outcome
    session.

  A regression test now covers the holiday case.
- **Secondary session alignment:** "primary 10-13 vs lastTradeDate 10-12" is a valid
  same-session match.
  - On every capture 10-01..10-14, official volume equals primary volume on the
    writeTime date for about 98–99% of rows (e.g. 204/205).
  - It equals primary volume on the lastTradeDate date for 0% of rows.
  - `lastTradeDate` is the date of the previous close, not of the session.
- **The 10-08 capture was stale.** It carried writeTime 10-07 and matched the 10-07 bars
  204/205. It was correctly classified SECONDARY_STALE (223 rows) and never used as
  10-08 evidence.
- **One UNVERIFIED_AMBIGUOUS_SESSION row on 10-13** (lastTradeDate missing). Handled
  fail-closed.
- **EGX open semantics:** the open equals the previous close in 72% of all bars and 81%
  of 2026 bars; the official openPrice matches.
  - Fills are still tradable: all 143 fill prices lie inside the entry day's
    [low, high].
  - The opening-gap logic, however, carries little information. DATA SEMANTICS
    limitation, not a bug.
- **Learning contamination:** labels and features use the primary daily series only, so
  the secondary market watch cannot leak into the learning data.
- **Cost basis of the headline: OBSERVABILITY FIX.** Before this release, a reader could
  not tell that `expectancy_r` and `max_drawdown_r` were gross.

## I. Learning (weekly run 2026-10-10, 28 monthly folds, about 540 out-of-sample sessions, horizons 1/2/3)

| Model | IC h1 | IC h3 | P@10 h1 | Top-10 r3 | Verdict vs FC-BASE-v1 |
|---|---|---|---|---|---|
| FC-BASE-v1 (champion) | −0.005 | +0.003 | 0.112 | 0.74% | — |
| FC-BASE-SECTOR-v1 | −0.002 | +0.002 | 0.095 | 0.64% | DOES_NOT_BEAT_CHAMPION |
| FC-RIDGE-v1 | +0.011 | +0.012 | 0.122 | 1.47% | BEATS_CHAMPION (descriptive, small effect; no significance test stored; no promotion) |

The DF arms are judged against DF0 (technical). A challenger needs IC > 0 and a better
top-10 r3.

| Arm | Walk-forward IC | Top-10 r3 | Verdict |
|---|---|---|---|
| DF0 | −0.035 | 1.373% | Baseline |
| DF1 | −0.048 | 1.382% | DOES_NOT_BEAT |
| DF2 | −0.041 | 1.358% | DOES_NOT_BEAT |
| DF3 | −0.036 | 1.401% | DOES_NOT_BEAT |
| DF4 | −0.036 | 1.400% | DOES_NOT_BEAT; catalyst history UNKNOWN before 2026-10 |
| DF5 | −0.014 | 1.024% | DOES_NOT_BEAT |

- The live frozen snapshots (5 transitions) are INSUFFICIENT_SAMPLE.
- Promotion remains NONE: operator decision, at least 50 sessions out of sample across
  regimes.

## J. Recommendations

| Class | Finding | Evidence / n | Mechanism | Risk | Validation |
|---|---|---|---|---|---|
| WORKING AS DESIGNED | Fills, stops, targets, costs | 74 closed + 12 case cards | Reconciled with the bars | — | `audit_trades.py`, `audit_cases.py` |
| WORKING AS DESIGNED | Secondary alignment; 10-08 stale capture | 14 captures, about 205 rows each | writeTime-session volume equality | — | `mw_population.py` |
| WORKING AS DESIGNED | Learning labels / no look-ahead | 508,687 labels | Observed-session index | — | `audit_labels.py` + test |
| OBSERVABILITY (released) | Headline expectancy and drawdown are gross R | 74 | Mixed cost basis | Misreading performance | `expectancy_r_net`, `max_drawdown_r_net`, `r_basis` |
| OBSERVABILITY (released) | Repeat candidates of held tickers inflate trade counts | 66/74 overlap; 43 distinct events | V1 records each session's candidate | Overstated sample and drawdown | Independent-exposure fields; one-position-per-ticker rows per arm |
| DATA SEMANTICS | EGX open = previous close, so V2C is inert | 72% / 81% of bars | Exchange reference-price open | V2C gives no information | Document; consider the first trade/VWAP if a free intraday source qualifies |
| DATA (one-time) | STALE_DATA misses on 10-04 | 8 | Bootstrap ranking from 10-01 | None recurring | Monitor |
| STRATEGY HYPOTHESIS | One open position per ticker (no re-entry while held) | 45 vs 143 entries | Correlated duplicate exposure | Fewer trades | New challenger arm, pre-registered, forward only |
| STRATEGY HYPOTHESIS | Market-regime / breadth filter | Sell-off 10-11..10-13, breadth 0.08–0.15 | Trend entries bought into a fading rebound | Fewer trades in recoveries | New challenger, walk-forward on non-audit history first |
| STRATEGY HYPOTHESIS | Volume confirmation; rebound sub-model | Winners' rvol 2.94 vs 1.95; top missed winners oversold | — | Overfitting to n=4 | Walk-forward only, new challenger |
| STRATEGY HYPOTHESIS | Stop design (median 9.5% stop, 94.6% stopped) | 74 | Wide ATR stops still hit in a broad sell-off | — | Forward challenger only |
| INSUFFICIENT EVIDENCE | Resistance 1.8R gate value; score monotonicity; cohorts; V2D | 19 / 5 closed; 4 winners | — | — | Re-check at 50 and 100 closed trades |

None of the hypotheses was fitted on the audit sample.

## K. Release

Observability only. Every existing field keeps its value; the scratch re-run on production
data showed every existing metric, decision and attribution unchanged.

- **Lifecycles:** `simulate()` adds `r_multiple_net`.
- **V1 / US performance:** `performance()` adds `r_basis`, `expectancy_r_net`,
  `max_drawdown_r_net` (exit-date order) and the independent-exposure fields.
- **Experiment arms:** each arm adds `non_overlapping_*`, shown on the
  champion-vs-challenger page.
- **Tests:** regression tests cover the holiday-gap label, net vs gross reporting and
  independent exposure.

## L. Safety

- Paper/Shadow only; LIVE_MONEY=DISABLED.
- No strategy, threshold, weight, stop or target change; no promotion.
- No historical evidence deleted or rewritten.
- US strategy unchanged. The US chain on 10-14 ran normally: 500/500 symbols current and
  92 candidates.
- SEC data remains BLOCKED.
