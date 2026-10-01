# EGX ranking, system candidates and Paper/Shadow lifecycle

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED. A candidate is not a fill.

## Flow

```
TradingView daily acquisition (admitted, operator-accepted, no licence)
 → reviewed canonical pipeline (raw, validation, quarantine, immutable artifacts)
 → official market-watch cross-check (quarantine on discrepancy)
 → verified-session freshness (official index evidence)
 → EGX-RANK-v1 (app/strategies/egx_ranking.py), per symbol
 → system Paper/Shadow candidates (append-only ledger; human review optional)
 → lifecycle simulation (app/paper/system_candidates.py)
 → performance (closed lifecycles only)
 → ranking report → snapshot bundle → TODAY / SWING / PRE-SURGE / PERFORMANCE / SYSTEM
```

`python -m app.egx_ranking_run` runs the ranking, candidate and lifecycle
steps. It only reads the database. Its outputs are the report
(`egx-ranking.json`, written atomically) and the ledger
(`system-candidates.jsonl`, append-only, one record per symbol, session and
rule version).

## EGX-RANK-v1

- Inputs per symbol: the latest VALIDATED artifact of one provider (providers
  are never mixed). The file hash must match the artifact record. Only
  `VALID_EXECUTABLE` rows are used, and only bars dated on or before the
  scored session (the latest VERIFIED session).
- **Hard gates** (any failure gives NO_TRADE with the reason; a high score
  never overrides a gate): `SOURCE_NOT_ADMITTED`, `DATA_NOT_CURRENT`
  (freshness from verified sessions, the same rule as TODAY),
  `LAST_BAR_NOT_SESSION`, `INSUFFICIENT_HISTORY` (< 60 bars), `ILLIQUID`
  (20-session average traded value < 1,000,000 EGP), `ZERO_VOLUME_SESSION`.
- **Score** (0–100):

  | Component | Points |
  |---|---|
  | Trend (close > EMA20 > EMA50) | 30 if UP, 10 if NEUTRAL, 0 if DOWN |
  | Momentum (20-session return) | linear, 0 at ≤ 0 % to 25 at ≥ 15 % |
  | 20-session breakout | 15 if a breakout, 7 if within 3 % of the prior 20-session high, else 0 |
  | Volume (vs 20-session average) | 15 at ≥ 1.5×, 8 at ≥ 1.0×, else 0 |
  | Liquidity (average traded value) | 15 at ≥ 20 M EGP, 10 at ≥ 5 M, 5 at ≥ 1 M, else 0 |

- **Classes**:
  - STRONG_CANDIDATE: score ≥ 75, trend UP, breakout, volume ≥ 1.5×;
  - CANDIDATE: score ≥ 60 and trend UP;
  - WATCHLIST: score ≥ 45, or trend UP;
  - otherwise NO_TRADE (`DOWNTREND` / `LOW_SCORE`).
- **Plan**:
  - entry zone: close ± 0.5 %, next eligible session only;
  - stop: close − max(1.5 × ATR14, 3 % of close);
  - T1 = close + 1.5 R and T2 = close + 3 R;
  - R:R 1.5 to T1 and 3.0 to T2, with the risk % shown.
- **Per-symbol evidence**: ticker, company, ISIN, source, licensing,
  freshness, score and components, class, entry zone, stop, T1, T2, R:R,
  liquidity, momentum, trend, breakout, volume confirmation, rejection
  reasons, data warnings (for example `SPLIT_ADJUSTED_SERIES` and quarantined
  rows), last market session, artifact id and report timestamp.

## System candidates (operator decision 2026-10-01)

STRONG_CANDIDATE and CANDIDATE records become Paper/Shadow candidates with
`origin=SYSTEM_GENERATED` and `human_review=OPTIONAL_NOT_REVIEWED`.
`live_money` is false. Human review is an optional approval layer, not a
prerequisite. The reviewed manual SWING launch path (explicit human WATCH
selection) is unchanged and separate.

## Lifecycle simulation

The lifecycle is recomputed on every run from bars after the candidate
session only, so it cannot look ahead and re-running it is idempotent.
- **Entry** (next session):
  - open inside the zone fills at the open;
  - open above the zone fills at the zone top if the bar trades down to it,
    otherwise `NO_FILL_GAP_UP`;
  - open below the zone gives `NO_FILL_GAP_DOWN`;
  - no bar yet gives `PENDING_ENTRY`.
- **Costs**: 0.1 % slippage per fill and per exit, plus 0.15 % commission
  and fees per side.
- **Exits**:
  - if the stop and a target trade in the same bar, the stop is assumed first;
  - half exits at T1, then the remainder's stop moves to the entry price;
  - the rest exits at T2;
  - gaps fill at the open;
  - otherwise the position closes at the close of the 10th session after
    the fill bar.
- Statuses: `PENDING_ENTRY`, `NO_FILL_*`, `OPEN`, `CLOSED_STOP`,
  `CLOSED_BREAKEVEN`, `CLOSED_T2`, `CLOSED_TIME`. Each carries the gross R,
  net % after costs, MAE/MFE in R and sessions held.

## Performance

Computed from closed lifecycles only: count, wins, losses, win rate, average
win and loss, expectancy (R), summed net %, max drawdown (R) and average
holding sessions. Below 20 closed trades it reports `INSUFFICIENT_SAMPLE`
and shows counts only, never extrapolated metrics.

## Schedule (Africa/Cairo, Sunday–Thursday)

- **16:45**: official market-watch post-close capture
  (`app.data.egx_market_watch_capture`, cron marker `# EGX_MW_CAPTURE`).
  It is verification evidence for the session that just closed.
- **08:30**: pre-market chain `tools/egx_nightly.sh` (run from a pinned release
  with `sh`, marker `# EGX_DAILY_CHAIN`), in order and one run at a time:
  calendar maintenance → TradingView daily acquisition with the cross-check
  against the previous evening's capture → ranking and system candidates
  (entry session = today) → universe scan → verified snapshot publication.
  Each step's exit code is logged in `<state>/nightly/<date>.log`.

EGX trades Sunday to Thursday; **Friday and Saturday are closed**, and no
Friday or Saturday session is ever expected. A run on any day processes the
latest completed, VERIFIED session. The report and every new candidate
record:
- `based_on_session`: the completed session whose close was used;
- `prepared_on`: the Cairo date the run happened;
- `next_expected_session`: the next EGX weekday after it, skipping
  Friday/Saturday and any verified HOLIDAY. It is labelled "holiday status
  for this date not verified" because future holidays are never invented;
- `prepared_note`: for example "Based on the Thursday 2026-10-01 close;
  prepared on Friday 2026-10-02 for evaluation ahead of the next expected
  EGX session, Sunday 2026-10-04."

The one-off run on Friday 2026-10-02 at 08:30 is exactly that: it processes
Thursday 2026-10-01 for Sunday 2026-10-04. It is not a Friday session.

Why pre-market: candidates are produced before the session they would trade
in. Each run also has its own acquisition date, so immutable
(symbol, snapshot-date) artifacts never collide. The first evening run on
2026-10-01 collided with that morning's backfill: 135 symbols reported
`DEFERRED_SNAPSHOT_DATE_USED`, with nothing overwritten.

## Cross-check policy (EGX-XCHECK-v2)

Calibrated on the 2026-10-01 field study (TradingView against the official
post-close capture, n = 419 bar comparisons):

| Field | Exact | ≤ 0.5 % | ≤ 2 % |
|---|---|---|---|
| Open | 100 % | 100 % | 100 % |
| High | 98 % | 98 % | 99.5 % |
| Low | 44 % | 67 % | 96 % |
| Close vs official `lastPrice` | 79 % | 92 % | 99 % |
| Close vs official `closePrice` | 74 % | 88 % | 98 % |
| Volume | 62 % | 72 % | 82 % |

- Material (quarantine): open > 0.5 %, high > 2 %, low > 5 %, or close vs
  official `lastPrice` > 2 %.
- MINOR_DIFFERENCE: anything else above 0.5 %, stored with every difference
  recorded.
- Volume and the official weighted `closePrice` are report-only (definitional
  differences).
- The first run's 0.5 %-on-every-field rule quarantined 82 symbols that were
  mostly definitional differences in low and close. Those runs are recorded,
  not rewritten.
