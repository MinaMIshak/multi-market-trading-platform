# M7 - Paper Performance and User Surface

## Status

M7 is in progress.

The approved M7A core implements deterministic, offline, in-memory
performance aggregation over canonical M6 paper-simulation artifacts.

M7A does NOT implement the full M7 milestone yet.

Still pending in the M7 continuation:
- equal-period return boundary
- Sharpe
- Sortino
- slippage sensitivity
- PERFORMANCE user surface completion
- final M7 integration verification

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

## 15. Sharpe and Sortino - pending M7 continuation

Sharpe and Sortino are intentionally not implemented in M7A.

They must NOT be calculated from irregular individual trade P&Ls.

The M7 continuation must add an explicit equal-period return boundary.

That boundary must make explicit:

- period chronology
- per-period returns
- per-period risk-free return for Sharpe
- Sortino target/minimum acceptable return
- minimum sample requirement
- annualization factor, if annualization is requested

M7 must not invent:

- 252 trading days
- 12 months
- 365 days
- zero risk-free rate
- return frequency
- benchmark
- annualization factor

When a valid periodic series is absent, Sharpe and Sortino must be reported as
unavailable/null with an explicit reason.

Zero dispersion must not produce infinite Sharpe.

Zero downside deviation must not produce infinite Sortino.

---

## 16. Slippage sensitivity - pending M7 continuation

Slippage sensitivity is intentionally not implemented in M7A.

Scenarios must be explicit caller-supplied scenarios.

M7 must not invent arbitrary slippage grids.

Sensitivity comparisons must preserve the same underlying canonical:

- TradePlan
- RiskDecision
- bars
- admission time
- session identity
- market date
- source identity
- provenance identity
- strategy identity
- regime identity

Only explicitly authorized execution-slippage assumptions may differ.

A sensitivity result is descriptive paper analysis only.

A small scenario grid must not be labelled as proof of robustness.

---

## 17. PERFORMANCE UI - partial implementation

A minimal truthful PERFORMANCE surface is implemented.

Current behavior includes:

- `/performance` HTML route
- pure `render_performance_dashboard(...)` presenter
- complete target navigation
- TODAY -> PERFORMANCE navigation link
- explicit PAPER / Observation Mode labeling
- truthful empty state when no real M7 report is available
- no synthetic trades or recommendations
- display of currently implemented M7A summary and realized drawdown fields
  when a real `PerformanceReport` is supplied
- explicit disclaimer that paper measurements are not profitability,
  strategy-validation, robustness, alpha, or real-money-readiness evidence

The route intentionally has no fake or demo performance dataset attached.
Without a real report it renders the unavailable state.

The presenter consumes already-computed M7 report models.

It does not independently recompute:

- fills
- trade outcomes
- expectancy
- drawdown
- strategy logic

The remaining M7 continuation must extend the surface after the corresponding
report fields exist to truthfully show:

- full state counts where useful
- monthly consistency
- regime consistency
- Sharpe value or unavailable reason
- Sortino value or unavailable reason
- slippage sensitivity

Those additions must continue to consume canonical M7 report objects rather
than duplicating analytics inside the HTML layer.

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
