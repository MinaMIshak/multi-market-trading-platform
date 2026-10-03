# EGX champion vs challenger experiment (EGX-EXP-v1)

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED. A candidate is not a fill.

**Purpose:** test, on identical evidence, whether a tradeability and risk layer
improves **expectancy and risk-adjusted Paper/Shadow performance**. Win rate
alone never decides. The champion (V1 = EGX-RANK-v1) is unchanged and stays
the champion until evidence supports promotion. There is no auto-promotion.

## Three separate concepts

| Concept | Values | Source |
|---|---|---|
| Technical class | STRONG_CANDIDATE / CANDIDATE / WATCHLIST / NO_TRADE | EGX-RANK-v1, never modified |
| Trade eligibility | ELIGIBLE / BLOCKED / UNKNOWN (NOT_READY) | challenger gates at decision time, per arm |
| Final Shadow action | PAPER_ENTRY / WATCH / NO_TRADE / EXPIRED / CHASE_BLOCKED / INVALIDATED | eligibility plus the entry session |

## Arms

| Arm | Definition |
|---|---|
| V1 | Champion EGX-RANK-v1, unchanged: STRONG_CANDIDATE / CANDIDATE with V1 lifecycle rules |
| V2A | V1 + position-aware liquidity / tradeability gate |
| V2B | V2A + room-to-resistance gate (≥ 1.8R) |
| V2C | V2B + entry-zone validity, gap/chase protection, setup expiry |
| V2D | V2C + risk-based Paper sizing with a portfolio open-risk cap |

## Same-evidence design (`app/egx_experiment_run.py`)

- **Inputs:** the V1 candidate ledger, the V1 report and the validated daily
  artifacts. All are read-only; V1 files are never written.
- **Decision record:** for each V1 candidate, decision-time evidence is
  computed once from the exact artifact V1 used (`artifact_id`), with bars
  dated ≤ the candidate session only. It is appended to
  `state/egx-experiment/decisions.jsonl` under (candidate, config version).
  Every arm reads that one record: the same symbols, bars, session and
  timestamps.
- **Lifecycles:** recomputed from the latest validated bars after the session,
  exactly as V1 does, through the same simulator and the same cost
  assumptions (V1 slippage 0.1%, commission 0.15% per side).
- **V2C additions:** V2C adds only the pre-fill checks on the entry session's
  first bar (open first).
- **Parity check:** the V1 arm is recomputed and compared with the V1 report's
  lifecycles (`health.v1_lifecycle_parity_mismatches`, expected empty).
- **Versioning:** every row carries `strategy` and `config_version`.

## Versioned configuration (`app/strategies/egx_experiment_config.py`)

`EGX-EXP-CFG-1`, effective 2026-10-03. Thresholds were fixed before any
challenger outcome existed. A change means a new version, never an edit, and a
test pins the values.

| Threshold | Value | Rationale |
|---|---|---|
| Model capital | 1,000,000 EGP | round research capital, not an account |
| Risk per trade | 0.5% | conservative start; alternatives become new versions |
| Max position | 20% of capital | concentration cap |
| Portfolio open risk (V2D) | 3% | at most six simultaneous 0.5% risks |
| Min average turnover | 1,000,000 EGP/day | the EGX-RANK-v1 ILLIQUID floor (unchanged) |
| Max position / 20-session average turnover | 5% | position-aware participation default |
| Resistance lookback | 250 sessions | about one EGX trading year |
| Pivot half-width | 3 sessions | confirmed pivots only |
| Min room to resistance | 1.8R | requested initial threshold, above V1's 1.5R T1 |
| Max chase above the zone | 1.0% | twice the V1 entry half-band (0.5%) |
| Entry window | 1 session | same as V1 (next eligible session only) |

## Gates

**Liquidity** (`liquidity_evidence`, `liquidity_gate`):
- Evidence: current volume; the 20-session average volume (the prior 20, the
  V1 relative-volume basis); relative volume; turnover = close × volume; the
  20-session average turnover (the V1 basis).
- PASS requires volume on the session, average turnover ≥ the floor, and the
  planned position ≤ 5% of average turnover.
- Fewer than 21 sessions or a missing volume gives UNKNOWN / NOT_READY, never
  PASS.

**Sizing** (`position_size`):
- Intended entry = the top of the entry zone (the worst in-zone fill).
- quantity = floor(min(risk budget / (entry − stop), 20% of capital / entry)).
- Recorded: capital, risk %, risk budget, entry, stop, risk per share,
  quantity, position value and planned risk.

**Resistance** (`pivot_highs`, `resistance_gate`):
1. Take the prior sessions (the decision bar is excluded) within the lookback.
2. A confirmed pivot high is a high ≥ every high within 3 sessions on both
   sides and strictly above the 3 before it. Its right side must already have
   printed on or before the session, so a fresh spike is not yet a pivot.
3. The nearest resistance is the lowest confirmed pivot high above the
   intended entry.
4. room R = (resistance − entry) / (entry − stop). PASS if ≥ 1.8R; otherwise
   `RESISTANCE_RR_FAIL`.
5. With no confirmed pivot above the entry, the gate passes with that stated
   reason and room is UNKNOWN; no level is invented.

**Entry, gap, chase and expiry** (`entry_state`, V2C/V2D, entry session only):

| State | Rule |
|---|---|
| INVALIDATED | open ≤ stop |
| BELOW_ENTRY | open < zone low (no fill; attribution `ENTRY_NOT_REACHED`) |
| ENTRY_VALID | open within the zone, or open ≤ 1.0% above the zone and the low trades back into it |
| CHASE_BLOCKED | open > 1.0% above the zone top: no fill even if price returns |
| SETUP_EXPIRED | open above the zone (≤ 1.0%) and never back in the zone during the window |
| WAITING_FOR_ENTRY | no entry session yet |

**Stale data:** if V1 freshness is not CURRENT, or the newest decision bar is
not the session, every challenger arm records `STALE_DATA`.

**Portfolio cap (V2D):** candidates are processed by (session, score desc,
ticker). An entry is refused with `PORTFOLIO_RISK_CAP` when the open planned
risk on its entry date plus the new risk would exceed 3%. Only exits known by
that date count.

## Attribution

Each row records why it was accepted or rejected: `PASS`, `LIQUIDITY_FAIL`,
`RESISTANCE_RR_FAIL`, `CHASE_BLOCKED`, `ENTRY_NOT_REACHED`, `SETUP_EXPIRED`,
`INVALIDATED`, `STALE_DATA`, `NOT_READY` or `PORTFOLIO_RISK_CAP`. For each arm,
the report lists the V1 closed trades it excluded, split into winners excluded
and losers avoided, with the R excluded. Comparing V1→V2A→V2B→V2C→V2D
isolates each gate's incremental effect.

## Metrics (PERFORMANCE)

Per arm:
- **Counts:** eligible setups, blocked, simulated trades, closed.
- **Outcome metrics:** win rate; expectancy, average and median net R; average
  winner and loser; profit factor; cumulative R; maximum drawdown (R); T1, T2
  and stop hit rates; average MAE and MFE (R); average holding.
- **Robustness:** expectancy at 2× costs (sensitivity); an approximate 95%
  interval for expectancy from 20 closed trades. V2D adds closed P&L in EGP
  and the return on model capital.
- **Net R** = net return × entry / (entry − stop).

**Sample policy:** below 20 closed trades the label is INSUFFICIENT_SAMPLE.
Then come EARLY_CHECKPOINT_20, CHECKPOINT_50 and CHECKPOINT_100. Promotion is
NOT_ELIGIBLE before at least 50 closed trades with robust expectancy, profit
factor and drawdown. The decision is the operator's.

## CPCI (session 2026-10-01): explanation and regression fixture

`tests/fixtures/egx_cpci_2026-10-01.json` holds 260 sessions from the V1
decision artifact `63b8859d…`. The fixture reproduces the V1 plan: entry zone
595.99–601.97, stop 573.47, relative volume 2.35×, 20-session breakout, 20-session
momentum 10.31%. Evidence from this platform:

| Item | Value |
|---|---|
| Technical class | STRONG_CANDIDATE, score 82.18 (unchanged) |
| Liquidity | 20-session average turnover 3,674,835.74 EGP; session turnover 8,672,631.42 EGP; volume 14,479 vs a 20-session average of 6,154.10 |
| Sizing | 175 shares at 601.97, about 105,345 EGP, planned risk 4,987.50 EGP |
| Liquidity gate | position 2.87% of average turnover: **PASS** |
| Resistance | confirmed pivot high **644.00** (2026-08-11); reward 42.03 vs risk 28.50 = **1.47R** < 1.8R: **FAIL** |
| Trade eligibility | ELIGIBLE in V2A; **BLOCKED (RESISTANCE_RR_FAIL)** in V2B–V2D |
| Final Shadow action | WATCH (in V2D) |

Measured from the 598.98 reference instead of the zone top, the room is 1.76R,
still below 1.8R. The other platform's conclusions were not assumed. The
values above come from this platform's artifact only. CPCI is an explanation
case, and no threshold was set from it.

## Operation

Nightly step `experiment` in `tools/egx_nightly.sh`, after `ranking`.
Runtime input `egx_experiment` (`egx-experiment.json`) is published by both
chains. The UI shows:
- **TODAY / SWING:** a "Trade (V2D)" column; the gate explanation is in the row
  details;
- **LIVE:** lifecycle states per arm;
- **PERFORMANCE:** the comparison and attribution;
- **SYSTEM:** versions, configuration, rationale and health.

PRE-SURGE is unchanged: an early-warning screen, never a Paper entry.
