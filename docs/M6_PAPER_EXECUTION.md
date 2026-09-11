# M6 deterministic paper execution V1

`app.paper.simulate_paper(PaperSimulationInput)` is pure, offline, in-memory
simulation. It changes no canonical model, database schema, execution state or
operational wiring. M4 Candidates cannot enter this boundary. Invalid inputs raise
TypeError/ValueError (including Pydantic ValidationError); valid M5 BLOCK returns
REJECTED with no position, fill, outcome or metrics. No trading evidence or
profitability/strategy-validation claim follows from these arithmetic fixtures.

## Contracts

- `PaperExecutionConfig`: required `config_version='paper-execution-v1'`, four
  explicit slippage rates, variable/fixed per-side costs, TARGET_1 selection,
  participation fraction and time/session boundaries. Optional controls must be
  supplied explicitly as None to disable. Decimal numeric inputs are finite and
  nonnegative; participation is (0,1]. Timestamps must be aware.
- `PaperSimulationInput`: required `schema_version='paper-simulation-v1'`, canonical
  TradePlan, canonical RiskDecision, tuple of canonical IntradayBar, admission_time,
  config, session_id, market_date, source_id and provenance_id. Models are revalidated
  at execution, including model_copy bypasses. Identity and chronology errors fail
  closed; input is never silently sorted. Bars must be final CONTINUOUS, contiguous,
  consecutively sequenced and nondecreasing in availability. Every non-empty replay
  must start at sequence 1 (canonical opening origin). Omitted opening history is
  rejected to prevent execution bias and false NO_FILL results. No exchange timezone
  or wall-clock hours are inferred.
- `PaperFill`: side, full approved quantity, raw/slipped prices, bar sequence,
  interval bounds, known_at=bar.available_at and at_open. A non-open fill has an
  interval, not an invented exact intrabar timestamp.
- `PaperPosition`: PAPER-only in-memory state, plan ID, quantity, entry and optional
  exit; OPEN/CLOSED. No random IDs or persistence.
- `PaperTradeMetrics`: two notionals, two costs, gross/net P&L, r_multiple, mae/mfe
  price distances and mae_r/mfe_r. Only completed trades have metrics.
- `PaperSimulationResult`: `paper-result-v1`, state, optional outcome/rejection
  reason/exit reason, optional position and optional completed-trade metrics.

TradePlan has no source/session identity fields and RiskDecision binds the plan by
ID. The caller must explicitly bind the intended stream's session/date/provenance;
M6 checks every bar against that declaration and the plan symbol. This is consistency
validation, not independent provenance authentication. Canonical bar validation is
structural; it is not market-data verification authority. No weaker shadow order or
bar model is introduced. Retain the input alongside its result for replay/audit.

## Event sequence

Admission must be inside plan validity. Only bars starting at/after admission and
ending at/before expiry can enter; straddling bars cannot fill. Replay is historical:
bar decisions become known at available_at; no observation clock is inferred. Later
bars never choose an earlier execution path. The complete supplied sequence is
validated first, so malformed later input rejects the request rather than silently
returning an earlier success.

Only LONG, positive APPROVE/REDUCE quantity/risk and matching plan ID are supported.
Stop must be below the whole entry zone, TARGET_1 above it. Entry fills at open when
inside the zone, entry_low when open is below, and entry_high when open is above
and low reaches the zone. The range must intersect. Participation floors capacity
and skips insufficient bars; no partial fills. Entry slippage increases price; a
slipped entry outside stop/target geometry rejects the simulation.

Subsequent bars check open <= stop (raw open), then open >= target (raw TARGET_1,
no favorable gap credit), then a due scheduled exit (raw open). Only then inspect
low/high, with stop winning when both levels touch. Sell-side slippage lowers price
using the rate for STOP, TARGET_1 or scheduled exit. Nonpositive/nonfinite fills fail.

Entry-bar low <= stop resolves pessimistically to a post-entry stop touch at stop,
never a pre-entry gap price. Target high is usable only for an entry at open; for a
non-open entry, close >= target is required to prove a later crossing. A possible
stop always wins. No future bar resolves uncertain intrabar ordering.

The earlier supplied time/session boundary wins (TIME_EXIT wins exact ties).
A boundary executes at the first bar starting at/after it; earlier-starting bars
cannot provide its open. Gap stop/target precedes scheduled exit at that open;
scheduled exit precedes later high/low. No new entry is initiated on a bar whose
open is already at/after a configured exit boundary. No session close is invented.

## States and arithmetic

NO_FILL requires the validated sequence to reach/pass valid_until without entry.
Early exhaustion is INCOMPLETE. An active position at exhaustion is OPEN with no
terminal outcome/metrics; no expiry liquidation is inferred. Terminal outcomes are
WIN for target, LOSS for stop and TIME_EXIT for explicit time/session exit. These
labels identify exit mechanism, not the sign of net P&L. Rejection is not NO_FILL.

Decimal arithmetic uses a private fixed 34-digit context, independent of ambient
precision/rounding. M4 float prices/volume convert through their decimal string.
No currency/tick rounding policy is invented. Each side costs notional * bps/10000
+ fixed_cost. Gross P&L is quantity*(exit-entry); net subtracts both costs. R uses
exactly M5 approved_risk, without replacing it with realized stop distance.

MAE/MFE are nonnegative price distances from slipped entry, excluding fees; R
versions multiply by approved quantity and divide by approved risk. Surviving
subsequent bars contribute full extrema. A surviving entry bar contributes its low
(pessimistic possible post-entry adverse movement), but its high only if entry was
at open; otherwise close is the favorable sample. Terminal bars contribute only
an already-existing position's open plus raw/slipped exit. Target gaps cap favorable
excursion at TARGET_1. No terminal high/low/close is consumed. This explicitly
censored convention can understate actual terminal-bar adverse/favorable extrema;
it is not tick-path reconstruction and must not be interpreted as exact tick MAE/MFE.

No partial exits, shorts, tick ordering, queues, liquidity beyond participation,
portfolio reservation, resume protocol or cross-session replay are implemented.
OPEN/INCOMPLETE runs can be replayed with an extended valid sequence. Costs/slippage
are caller-supplied simulation assumptions. M7 and operational integration remain
outside scope.
