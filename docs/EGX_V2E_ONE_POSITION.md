# EGX challenger V2E: one open position per ticker (pre-registered)

| Item | Value |
|---|---|
| Identity | `EGX-EXP-V2E-ONE-POSITION-v1`, arm `V2E`, config `EGX-EXP-V2E-CFG-1` |
| Pre-registered | 2026-10-15 |
| Code | `app/strategies/egx_one_position.py`, evaluated by `app/egx_experiment_run.py` |
| Origin | The EGX strategy audit (`docs/EGX_STRATEGY_AUDIT_2026-10-14.md`) |
| Mode | Paper/Shadow research only; LIVE_MONEY=DISABLED; not promoted |

## Hypothesis

Preventing concurrent duplicate exposure to one ticker improves risk-adjusted Paper/Shadow
performance. The audit found that 66 of 74 closed V1 lifecycles overlapped another lifecycle
of the same ticker. That observation motivated the rule. It is not evidence of edge, and the
rule was not tuned on it.

## Rule (exact semantics)

V2E inherits the champion **V1 (EGX-RANK-v1) unchanged**:

- the same candidates (STRONG_CANDIDATE / CANDIDATE);
- the same eligibility;
- the same entry zone, stop, T1/T2 and breakeven rules;
- the same 10-session time exit, simulator and EGX costs.

Each V2E lifecycle is a copy of the V1 lifecycle for that candidate. V2E adds one rule:

> When the decision for a candidate on session S is made (after S closes), its ticker may already
> hold a V2E lifecycle that has not reached a terminal state by S. That covers a lifecycle still
> pending entry, or one filled and not yet exited. The candidate is then `BLOCKED` with
> `ALREADY_OPEN_POSITION`.

- **Terminal states:** `EXIT_STOP`, `EXIT_BREAKEVEN`, `EXIT_T2`, `EXIT_TIME`, `NO_FILL_GAP_DOWN`,
  `NO_FILL_GAP_UP`. Only events dated on or before S decide the state, so the rule uses no
  information from after the decision.
- **The signal is never discarded.** A blocked row keeps the ticker, session, technical class
  and score, plus `blocking_position`: the earlier candidate, its signal session, entry date and
  state at decision. The Opportunity scores, forecasts and context stay in their own reports.
- **A ticker becomes eligible again** after its V2E lifecycle closes or fails to fill.
- **Other tickers** are unaffected.
- **No other changes:** no cooldown, re-entry delay, ticker exception, position limit above one,
  or score threshold.
- **No V2A–V2D gates.** V2E is compared with V2D (portfolio risk cap), never combined with it.
  A combined arm would be a separate future version.
- **No other hypotheses mixed in:** market regime/breadth, rebound model, volume threshold, stop
  design and resistance changes stay out of this arm.

## Samples and forward start

- **Registration:** `<state>/egx-experiment/v2e-registration.json` is written once at deployment
  (`python -m app.egx_experiment_run --register-v2e-only --candidates … --state …`). It holds:
  - `deployed_at`, the build revision, rank version and data version;
  - `last_retrospective_session` (the newest ledger session at deployment);
  - `first_forward_session`, set when the first later V1 report session is evaluated.
- **Forward log:** every later V1 report session is appended once to `v2e-forward-sessions.jsonl`,
  with a breadth-regime label. The label (sign of the universe's median 20-session momentum) is
  used only for the promotion checkpoint and never as a trade filter.
- **FORWARD_OUT_OF_SAMPLE:** candidates generated at or after `deployed_at`, for sessions on or
  after `first_forward_session`. This is the official scorecard.
  - The forward V2E book starts flat.
  - Every arm (V1–V2E) is scored on exactly these forward candidates.
  - Nothing is backfilled.
- **RETROSPECTIVE_DIAGNOSTIC:** every earlier candidate, in its own book that also starts flat.
  It only checks the implementation.

## Metrics (report key `challengers.V2E`)

**Per arm and sample:**

- **Counts:** candidate signals, eligible signals, duplicate-ticker blocks, independent entries.
- **Positions:** open, closed, wins, losses, no fills, pending entries.
- **Outcomes:** win rate; net expectancy (with the gross before-costs value alongside); average
  and median R; cumulative R; profit factor; max drawdown (R, net); average holding.
- **Hit rates and excursions:** T1, T2 and stop hit rates; MFE and MAE.

**Blocked duplicates:** each one gets the signal session, the existing position (entry, state,
outcome), technical and Opportunity scores, 1/2/3-session returns, and the hypothetical V1
outcome with its MFE/MAE. It is then classified:

| Classification | Meaning |
|---|---|
| `DUPLICATE_LOSS_AVOIDED` | The duplicate would have lost. |
| `MOVE_ALREADY_CAPTURED_BY_OPEN_POSITION` | A winning duplicate whose first winning event fell while the existing position was still held. |
| `INCREMENTAL_WINNER_EXPOSURE_BLOCKED` | A winning duplicate after the existing position had exited. |
| `WOULD_NOT_HAVE_FILLED` | The duplicate would not have filled. |
| `UNRESOLVED` | The duplicate's outcome is still open. |

**Exposure (V1, V2D, V2E):** positions held at each session close:

- simultaneous positions;
- positions on the same ticker;
- gross exposure, open planned risk;
- single-ticker exposure;
- largest sector share (sectors from the official market watch);
- ticker HHI.

Sizes use the EGX-EXP-CFG-1 model (0.5% risk of 1M EGP) as a common measurement notional, not an
account. A diversification claim is made only when these numbers support it.

## Verdicts and promotion (never automatic)

| Verdict | Condition |
|---|---|
| `INSUFFICIENT_SAMPLE` | Fewer than 20 forward sessions or fewer than 20 closed V2E positions. It also applies when V2E is ahead but has not yet reached 50 forward sessions spanning more than one regime. |
| `DOES_NOT_BEAT_CHAMPION` | Net expectancy not above V1's on the same forward candidates, or profit factor lower. Lower drawdown alone never qualifies. |
| `PROMOTION_CANDIDATE` | Ahead of V1 on both, with at least 50 forward sessions that span more than one regime. The operator decides; nothing is promoted automatically. |

- **Checkpoints:** 20, 50 and 100 forward sessions.
- **Top-K recall:** unchanged by construction, because V2E emits the same signals as V1.

## Retrospective diagnostic at registration (2026-10-15)

Implementation check only. This is not evidence and not the official scorecard. The ledger held
171 candidates (sessions 10-01..10-14).

| Arm | Closed | Wins | Net expectancy | Gross expectancy | Profit factor | Max DD (net) |
|---|---|---|---|---|---|---|
| V1 | 76 | 6 | −0.860R | −0.803R | 0.115 | −69.57R |
| V2D | 5 | 0 | −1.050R | −1.000R | 0.000 | −5.25R |
| V2E | 28 | 3 | −0.818R | −0.759R | 0.133 | −22.90R |

**Blocked duplicates (V2E): 122.**

| Classification | Count | R |
|---|---|---|
| DUPLICATE_LOSS_AVOIDED | 45 | −47.39R |
| MOVE_ALREADY_CAPTURED_BY_OPEN_POSITION | 3 | +4.96R |
| INCREMENTAL_WINNER_EXPOSURE_BLOCKED | 0 | — |
| UNRESOLVED | 74 | — |

**Exposure at each session close, 10-04..10-14:**

| Arm | Max simultaneous positions | Max on one ticker | Max gross exposure (EGP) | Mean ticker HHI |
|---|---|---|---|---|
| V1 | 102 | 8 | 6.11M | 0.053 |
| V2E | 38 | 1 | 2.28M | 0.047 |
| V2D | 6 | 3 | 0.40M | 0.332 |

The largest sector share is 0.18 for both V1 and V2E, so V2E reduced duplicate exposure but not
sector concentration.
