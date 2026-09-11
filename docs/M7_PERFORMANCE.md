# M7 - Paper Performance and User Surface

## Status

M7 implements deterministic, offline, in-memory performance aggregation over
canonical M6 paper-simulation artifacts. The approved M7A core and M7B UI
foundation are preserved, with optional periodic-return and explicit
slippage-sensitivity boundaries and their presentation added.

This module does not establish profitability, alpha, strategy validity,
robustness, or real-money readiness.

M8 owns research validation such as walk-forward analysis, frozen holdout
evaluation, bootstrap/confidence analysis, and other out-of-sample evidence.

---

## 1. Canonical performance boundary

A performance observation is not a loose dictionary of trade results.

`PerformanceObservation` binds:

- canonical `PaperSimulationInput`
- corresponding canonical `PaperSimulationResult`
- explicit `MarketRegimeType`
- explicit `strategy_id`
- explicit `strategy_version`

Schema identity:

`performance-observation-v1`

The M6 input and result must remain canonical model objects.

The observation boundary reconstructs the supplied canonical M6 objects
through their normal validation boundaries rather than accepting flattened
shadow dictionaries.

The supplied paper result is verified by deterministically running:

`simulate_paper(paper_input)`

The supplied result must exactly equal the deterministic M6 result.

A mismatched or fabricated result is rejected.

---

## 2. Analysis boundary

`PerformanceAnalysisInput` uses:

- schema version `performance-analysis-v1`
- canonical `PerformanceConfig`
- tuple of canonical `PerformanceObservation`

`PerformanceConfig` uses:

- config version `performance-v1`
- explicit positive `starting_equity`

Starting equity must be finite.

NaN, positive infinity, and negative infinity are rejected.

No default account equity is invented by M7A.

Duplicate observations sharing the same `trade_plan_id` are rejected.

---

## 3. Completed and non-completed observations

Only M6 results with:

`state == COMPLETED`

enter economic P&L metrics.

The following states are counted but do not enter economic return metrics:

- REJECTED
- NO_FILL
- OPEN
- INCOMPLETE

They are not treated as zero-return trades.

This prevents unavailable or unresolved paper outcomes from silently
contaminating expectancy, profit factor, equity, or drawdown.

---

## 4. Economic outcome classification

Economic wins and losses are classified from actual M6 `net_pnl`.

A completed observation is:

- economic win when `net_pnl > 0`
- economic loss when `net_pnl < 0`
- breakeven when `net_pnl == 0`

M6 mechanism labels do not replace this economic classification.

In particular, a M6 `TIME_EXIT` result is classified economically from the
sign of its actual `net_pnl`.

A TIME_EXIT may therefore be:

- an economic win
- an economic loss
- breakeven

depending on realized paper P&L.

---

## 5. Core P&L metrics

For completed observations only:

### Total gross P&L

Sum of M6 `gross_pnl`.

### Total costs

For each completed observation:

`entry_cost + exit_cost`

Total costs are the sum across completed observations.

### Total net P&L

Sum of M6 `net_pnl`.

### Net expectancy

`sum(net_pnl) / completed_count`

Undefined when there are no completed observations.

### R expectancy

`sum(r_multiple) / completed_count`

Undefined when there are no completed observations.

The M7 layer consumes the exact M6 `r_multiple`.
It does not reconstruct risk independently.

---

## 6. Profit factor

Let:

- gross gains = sum of positive completed `net_pnl`
- gross losses = absolute value of summed negative completed `net_pnl`

Normal case:

`profit_factor = gross_gains / gross_losses`

Explicit edge semantics:

- no completed observations -> null
- completed sample with no economic losses -> null
- losses exist and no positive gains -> 0
- gains and losses exist -> normal ratio

M7A does not return infinity for a zero-loss sample.

---

## 7. Average win, average loss, and win rate

### Average win

Mean of completed `net_pnl` values strictly greater than zero.

Undefined if there are no economic wins.

### Average loss

Mean of completed `net_pnl` values strictly less than zero.

Undefined if there are no economic losses.

### Breakeven count

Count of completed observations whose `net_pnl == 0`.

### Economic win rate

`economic_wins / completed_count`

Breakeven completed trades remain in the denominator.

Undefined if there are no completed observations.

---

## 8. MAE and MFE

M7A aggregates M6 metrics directly.

It does not reconstruct intraday excursion from bars.

Reported averages are:

- average MAE
- average MFE
- average MAE in R
- average MFE in R

These are means over completed observations only.

The exact M6 MAE/MFE convention remains authoritative.

---

## 9. Realized completed-trade equity curve

M7A drawdown is a realized paper-equity convention.

It is not mark-to-market portfolio drawdown.

The equity curve starts from explicit `starting_equity`.

For each completed trade in deterministic exit order:

`equity_next = equity_previous + net_pnl`

Only completed realized paper outcomes modify the curve.

OPEN, INCOMPLETE, NO_FILL, and REJECTED observations do not create zero-return
equity points.

---

## 10. Deterministic trade ordering

Completed observations are ordered using:

1. exit fill `known_at`
2. exit fill `interval_start`
3. string form of `trade_plan_id`

This provides deterministic ordering when exits share timestamps.

Tuple input order is not relied upon where this canonical exit ordering is
available.

This ordering does not claim exact tick-level sequencing.

---

## 11. Drawdown convention

The running peak starts at `starting_equity`.

For each completed trade:

`drawdown_amount = running_peak - current_equity`

`drawdown_pct = drawdown_amount / running_peak`

M7A reports:

- starting equity
- ending equity
- peak equity
- maximum realized drawdown amount
- maximum realized drawdown percentage

This is completed-trade realized equity drawdown only.

It does not include intratrade mark-to-market drawdown.

---

## 12. Monthly consistency

Monthly grouping uses the canonical M6 `market_date`.

The key is:

`YYYY-MM`

Year and month are both retained.

Missing months are not inserted.

For each observed completed month, the normal economic summary is calculated.

Monthly consistency reports:

- evaluated month count
- positive months
- negative months
- flat months

Month classification is based on that month's total completed-trade
`net_pnl`.

---

## 13. Regime consistency

Regime identity is explicit input to `PerformanceObservation`.

M7A does not infer regime from future price action or realized outcomes.

Observations are grouped by the supplied canonical `MarketRegimeType`.

For each regime, the normal economic summary is calculated.

Non-completed observations remain visible in regime state counts, but they do
not enter economic P&L calculations.

No causal conclusion about a regime is implied by this grouping.

---

## 14. Numeric behavior

Performance aggregation uses Decimal arithmetic.

The analyzer uses a private local Decimal context with precision 34.

No hidden floating-point conversion is required for the existing M7A core.

No random state, wall clock, network data, provider state, or environment
state is used by the performance analyzer.

---

## 15. Periodic-return Sharpe and Sortino

`PeriodicReturnSeries` requires canonical `periodic-return-v1`, an explicit
`series_id`, positive integer `period_seconds`, ordered UTC `period_ends`,
and a matching tuple of finite Decimal `returns`. Each return describes the
fixed-duration interval ending at its corresponding timestamp. Adjacent ends
must differ by exactly `period_seconds`; duplicate, reversed, gapped, naive,
non-UTC, or conflicting chronology is rejected before aggregation. No sorting
repairs input. This boundary supports fixed elapsed durations only, not variable
calendar months or exchange-session calendars.

The caller supplies fractional simple periodic returns, an explicit constant
per-period `risk_free_return`, an explicit constant `sortino_target`, and an
integer `minimum_samples >= 2`. No returns are derived from irregular trades.
The series is caller-attested input, retained in the report for audit; M7 does
not infer its derivation or establish that it represents the trade equity curve.

Let n be the observation count, r_i the periodic returns, f the supplied
per-period risk-free return, and t the supplied minimum acceptable return.

- Sharpe: `mean(r_i - f) / sqrt(sum(((r_i - f) - mean(r_i - f))^2)/(n-1))`.
  The denominator is the **sample** standard deviation of excess returns.
- Sortino: `mean(r_i - t) / sqrt(sum(min(r_i - t, 0)^2)/n)`.
  The denominator is the root mean squared negative deviation from the target,
  using **all n periods**, including zero contributions for non-downside periods.

Both calculations use the analyzer's private Decimal context, precision 34,
including `Decimal.sqrt()`. Rounding follows that context (ROUND_HALF_EVEN).
If an explicit finite positive `annualization_factor` A is supplied, the
per-period ratio is multiplied by `sqrt(A)`. Otherwise the ratio remains
per-period. The caller owns the appropriateness of A; no 252, 12, 365, frequency,
benchmark, zero risk-free rate, or annualization assumption is invented.

`PerformanceReport.risk_adjusted` always exists. Reasons are:

- `NO_PERIODIC_SERIES`: no series supplied; both ratios null.
- `INSUFFICIENT_SAMPLES`: n below the caller's minimum; both ratios null.
- `ZERO_DISPERSION`: zero Sharpe denominator; Sharpe null.
- `ZERO_DOWNSIDE_DEVIATION`: zero Sortino denominator; Sortino null.
- Otherwise the corresponding reason is null and the ratio is a finite Decimal.

Malformed input raises an error, rather than returning an apparently usable
report. Canonical instances are reconstructed at the analysis boundary, including
model-copy inputs. Strict types and finite Decimal constraints reject unsafe
coercion and non-finite numbers. Extreme values exceeding the Decimal context
range also fail closed; no infinite ratios are emitted.

## 16. Explicit slippage sensitivity

`PerformanceAnalysisInput.slippage_scenarios` defaults to an empty tuple: no
hidden scenarios or grid. Every `SlippageScenario` requires version
`slippage-scenario-v1`, a nonblank canonical `scenario_id`, and a tuple of
canonical `PerformanceObservation` objects. Each embeds its explicit M6
`PaperExecutionConfig` and replay-verified result. Output retains the complete
scenario for audit.

Every scenario must contain exactly the original trade-plan ID set, with no
duplicates. Tuple ordering may differ; alignment is by trade-plan ID. Each
observation must preserve strategy ID/version and regime. All M6 input fields
must match exactly, including bars, TradePlan, RiskDecision (including sizing),
admission time, market date, session, source and provenance. Only these explicit
config fields may differ:

- `entry_slippage_bps`
- `stop_slippage_bps`
- `target_slippage_bps`
- `scheduled_exit_slippage_bps`

All other execution settings, including costs, volume participation and exit
boundaries, must match. Cost **assumptions** cannot change; cost amounts may
change when unchanged percentage fees apply to slipped notionals. M6 continues
to reject invalid/non-finite/negative slippage and impossible slipped prices.
No M6 object is mutated. Noncanonical or mismatched M6 results fail replay
verification. Duplicate scenario IDs, altered observation sets, unauthorized
changes, or a baseline ID absent from the supplied scenarios raise errors.

Each scenario reports completed count, total net P&L, net expectancy, profit
factor, realized max drawdown amount and fraction, using the unchanged M7A
completed-only conventions and the same explicit starting equity. Noncompleted
states never enter economics. Output is sorted by scenario ID.

If `baseline_scenario_id` explicitly identifies a supplied scenario, deltas are
`scenario - baseline` for total net P&L and net expectancy. Expectancy delta is
null if either expectation is undefined. Without a baseline both deltas are
null. An empty observation set has zero completed count, zero net P&L and
zero drawdown, with null expectancy and profit factor. This is descriptive
sensitivity, not strategy optimization or evidence of robustness.

## 17. PERFORMANCE UI

The approved `/performance` HTML route, pure
`render_performance_dashboard(...)`, full target navigation, and TODAY link
are preserved. No route or JSON endpoint is added. The route still has no data
source attached and renders the explicit unavailable state, without fabricated
trades, profits, ratios or recommendations.

When supplied an in-memory report, the pure presenter displays the existing
summary and realized drawdown plus state counts, MAE/MFE in R, monthly and regime
economics, periodic ratios or explicit unavailable reasons, periodic assumptions,
and supplied sensitivity metrics/deltas and baseline identity. Externally
supplied displayed identities are HTML escaped. Drawdown percentage values use
the documented fractional convention (0.1 means 10%).

The presenter only formats precomputed models; it never calculates fills,
expectancy, drawdown or strategy performance. PAPER / Observation Mode and the
no-strategy-validation disclaimer remain visible. Unbacked navigation tabs do
not manufacture results. TODAY is unchanged: validated data, read-only DB access,
fail-closed empty state, NO SETUP ENGINE, and trader-safe warning are preserved.

## 18. Safety boundary

M7 performance analysis is offline and in-memory.

M7 does not require:

- production writes
- DB/schema migrations
- EODHD
- external network access
- brokers
- live execution
- scheduler integration
- operational paper refresh
- secrets
- Docker control
- sudo
- deployment

The M7 performance layer does not modify M6 paper objects.

---

## 19. Interpretation limits

These metrics summarize deterministic M6 paper simulation.

They are not evidence by themselves of:

- future profitability
- alpha
- strategy validity
- statistical robustness
- production readiness
- real-money safety

Paper execution assumptions still govern the meaning of all measurements.

M8 is responsible for research validation, including appropriate
out-of-sample and walk-forward evidence.

M7 must stop before M8.
