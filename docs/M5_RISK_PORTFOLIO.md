# M5 Risk and Portfolio Engine

Engineering admission for research/paper planning only. All parameter values remain
**unvalidated research/paper policy choices**. Passing tests establishes contract
behavior, not profitability, real-world safety thresholds or execution readiness.

## Explicit boundary

Construct `RiskEngine(policy)` with a fully specified `RiskPolicy`. Every policy
field, including `policy_version`, scales, `allow_short` and the optional group cap,
is required. Explicit `None` disables the correlation-group cap; zero enables a
cap with no capacity. Existing range limits remain (per-trade risk at most 5%,
concurrency 1–20). Percentages are fractions of equity. Scales are in [0, 1];
neutral cannot exceed risk-on, and risk-off must be zero. Position, symbol and group
caps cannot exceed the total exposure cap; trade risk cannot exceed portfolio risk.
These are configuration consistency rules, not empirical validation.

`evaluate` requires a genuine validated `TradePlan`, positive finite Decimal equity,
market regime, `RiskContext`, aware `decision_time` and explicit `TradeState`.
A mapping or M4 Candidate raises TypeError; no conversion or operational wiring is
provided. Invalid typed data raises TypeError/ValueError (including Pydantic
ValidationError). Economically ineligible valid inputs return BLOCK.

Only READY and ENTRY_TRIGGERED enum values are eligible. Require
`created_at <= decision_time < valid_until`. No lifecycle state is changed.
Existing TradePlan validation enforces entry-zone, directional stop, increasing or
decreasing targets and expiry structure; Target 1 must meet the explicit minimum R.
RISK_OFF, UNKNOWN and unrecognized regimes block. Daily realized R at or below the
negative daily limit blocks. Shorts block unless explicitly permitted.

## Portfolio snapshot contract

Policy/context are strict, immutable, reject extra fields and non-finite numbers.
Supply Decimal values, actual integer counts and actual bool controls. Required
context fields are open_positions, pending_entries, current_exposure_value,
daily_realized_r, cash_balance, reserved_cash, current_open_risk_value and
symbol_exposure_value. Optional liquidity_cap_value may be zero (blocking).
All balances/exposures/counts are nonnegative except signed daily_realized_r.
Reserved cash cannot exceed cash; symbol/group exposure cannot exceed total
exposure. Group identity and exposure must be supplied together. An enabled group
cap without group context blocks.

The caller must supply a coherent snapshot for the account, symbol, group and
trading day at decision_time. Exposure and open-risk values must include outstanding
commitments, not only filled positions; pending_entries counts outstanding new-entry
commitments. Cash reservations cover committed cash. The engine cannot authenticate
snapshot freshness, completeness or group membership. It computes no statistical
correlations. For grouped portfolios this contract assumes one trusted group per
proposed entry. Overlapping multi-group risk requires future separately scoped work.

Concurrency is `open_positions + pending_entries < max_open_positions`.
Admission does not reserve cash/capacity or provide atomic multi-request allocation;
callers must serialize admission and update commitments before further requests.

## Independent admission caps

Each cap is independently floored to whole shares, clamped at zero, then the
smallest quantity wins:

| Audit cap | Numerator | Divisor |
| --- | --- | --- |
| RISK_BUDGET | equity × risk_per_trade_pct × regime scale | risk_per_share |
| POSITION_CAP | equity × max_position_pct | entry_reference |
| PORTFOLIO_EXPOSURE | equity × max_portfolio_exposure_pct − current_exposure_value | entry_reference |
| CASH | cash_balance − reserved_cash | entry_reference |
| PORTFOLIO_OPEN_RISK | equity × max_portfolio_open_risk_pct − current_open_risk_value | risk_per_share |
| SYMBOL_EXPOSURE | equity × max_symbol_exposure_pct − symbol_exposure_value | entry_reference |
| CORRELATION_GROUP_EXPOSURE | equity × group cap − group exposure (when enabled) | entry_reference |
| LIQUIDITY | liquidity_cap_value (when supplied) | entry_reference |

`risk_per_share = abs(entry_reference − stop_price)`. Cash and exposure use
entry_reference only as a planning reference, including explicitly permitted short
research plans. No margin/short-proceeds model is implied. No fees, slippage, fills,
gaps, simulator, Position, TradeOutcome or state transition is created here.

Every active cap below the risk-budget quantity contributes a stable
`REDUCED_BY_<cap>` reason, even when a tighter cap wins. Zero caps also record
`NO_CAPACITY_<cap>`. Zero final quantity blocks with zero approved risk and value.
Approved risk is quantity × risk_per_share and cannot exceed the scaled budget.
Gate blocks precede sizing and have empty quantity_caps. Existing reason names for
regime, position, total exposure and liquidity are retained.

## Deterministic audit and compatibility

RiskDecision requires policy_version and policy_identity and exposes quantity_caps.
Identity is SHA-256 of sorted canonical JSON covering every policy field, with
numerically equivalent Decimal representations canonicalized. Replay with identical
inputs preserves all economic fields, caps, reasons and blockers; only decision UUID
may differ. No wall-clock time or provider is consulted. Callers should retain the
input plan, context, decision time and lifecycle state with their research audit.

The existing risk module and domain contracts are extended in place. The existing
storage JSON payload can carry the new audit fields without SQL/schema changes.
Old RiskDecision JSON lacking policy metadata needs explicit provenance handling if
later rehydrated; M5 does not invent historical identity or migrate stored data.
No storage, scheduler, provider, calendar, strategy or execution integration changes.

## Verification and boundary

Tests preserve existing behavior and cover explicit inputs, invalid configuration,
time/lifecycle gates, concurrency, each capacity, simultaneous caps, short/regime/
loss/RR/liquidity gates, deterministic replay and the Candidate boundary. The full
suite's autouse network guard blocks socket connection calls; tests use synthetic
fixtures and isolated test databases. Exact command results are in AGENT_STATUS.md.

M5 stops before M6. Executable pricing, fills, costs, slippage, simulation and
performance validation remain outside this milestone and require separate authority.
