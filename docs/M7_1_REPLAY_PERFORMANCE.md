# M7.1 — Multi-session replay performance

M7.1 adds pure, offline performance analysis over shared M6.1 replay contracts.
It does not change legacy M6, M7, M8, UI routes, schemas, or operational flows.
Its engineering tests do not establish historical performance or strategy validity.

## API and evidence binding

Import `analyze_replay_performance` from `app.performance.replay` and the new
contracts from `app.performance.replay_models`:

- `ReplayPerformanceObservation`: canonical `PaperReplayInput` and
  `PaperReplayResult`, plus explicit `strategy_id`, `strategy_version`, and
  `market_regime`. Schema: `replay-performance-observation-v1`.
- `ReplayPerformanceAnalysisInput`: canonical `PerformanceConfig`, a tuple of
  observations, optional explicit `PeriodicReturnSeries`, replay slippage
  scenarios, and optional `baseline_scenario_id`.
  Schema: `replay-performance-analysis-v1`.
- `ReplaySlippageScenario`: canonical scenario ID and tuple of observations.
  Schema: `replay-slippage-scenario-v1`.
- `ReplayPerformanceReport`: the aggregate `performance: PerformanceReport`,
  replay-specific `slippage_sensitivity`, and `baseline_scenario_id`.
  Schema: `replay-performance-report-v1`.

Every observation reconstructs the canonical replay input and result and
requires exact equality with `simulate_paper_replay(replay_input)`. Analysis
revalidates all observations and scenarios again, including `model_copy`,
`model_construct`, and mutated nested domain models. Dictionary substitutions,
invalid exact types, noncanonical identities, and mismatched results fail closed.
No caller-supplied P&L cache is trusted. Duplicate `trade_plan_id` observations
are rejected, including noncompleted observations.

For already canonical caller-owned evidence:

```python
from app.performance.replay import analyze_replay_performance
from app.performance.replay_models import (
    ReplayPerformanceAnalysisInput,
    ReplayPerformanceObservation,
)

observation = ReplayPerformanceObservation(
    replay_input=replay_input,
    replay_result=replay_result,
    strategy_id=strategy_id,
    strategy_version=strategy_version,
    market_regime=market_regime,
)
report = analyze_replay_performance(ReplayPerformanceAnalysisInput(
    config=performance_config,
    observations=(observation,),
))
summary = report.performance.summary
months = report.performance.months
```

## Report reuse and arithmetic

`PerformanceReport` is reused for summary, realized drawdown, monthly/regime
consistency, and optional periodic-return metrics. The legacy report's
slippage entries embed legacy `PerformanceObservation` objects whose results
must be recomputed through `simulate_paper()`. Replay scenarios therefore live
in the new wrapper as `ReplaySlippageSensitivityResult` objects, retaining their
complete replay observations. The embedded aggregate report has empty legacy
sensitivity and no legacy baseline. Consumers must use the wrapper to retain
replay sensitivity. No replay observation is converted into legacy M6 evidence.

The unchanged M7 state/metrics-only `_summary` helper is reused through a private
immutable view of validated replay results. This retains completed-only gross
and net P&L, costs, net/R expectancy, profit factor, average win/loss, economic
win/loss/breakeven classification, and MAE/MFE in price and R units. Excursions
come directly from M6.1's conservative OHLC convention.

OPEN, INCOMPLETE, NO_FILL, and REJECTED observations contribute state counts
only. They contribute neither realized P&L nor zero-return samples. Missing
realized months are not filled. Regime groups use the supplied regime, never an
inferred regime conditioned on later outcomes.

Completed drawdown events are ordered by:

1. Exit `known_at_utc`.
2. Exit `interval_start_utc`.
3. String form of `trade_plan_id`.

Monthly realized P&L uses the exit fill's explicit local `market_date`, including
when entry was in another month or the exit becomes known in a later month.
It never uses admission date, first-session date, or UTC availability month.
Drawdown is completed-trade realized equity drawdown, not mark-to-market risk.

Arithmetic and revalidation run in a private precision-34 Decimal context with
default half-even rounding, independent of ambient precision, rounding, traps,
and exponent bounds. Aggregate sums use trade-plan ID order for deterministic
reduction; drawdown uses the exit ordering above. No execution bars, calendars,
or availability timestamps are sorted, repaired, converted to float, or inferred.
Optional periodic-return statistics reuse M7's explicit caller-supplied series;
no regular returns or annualization factors are synthesized from trade results.

## Slippage scenarios

Each scenario must contain exactly the original trade-plan ID set once each,
and must preserve strategy ID/version and market regime. Only these execution
configuration fields may differ:

- `entry_slippage_bps`
- `stop_slippage_bps`
- `target_slippage_bps`
- `scheduled_exit_slippage_bps`

Every other replay input/config field is compared, including TradePlan,
RiskDecision, admission time, instrument/venue identity, calendar membership,
bars, per-bar availability, source/provenance, price basis, timezone, and path
coverage. Cost assumptions, volume participation, and scheduled exit times
cannot change. Unchanged percentage fees may produce different absolute costs
when slipped notionals change. Every scenario result is independently recomputed.

Sensitivity uses completed-only economics and the same starting equity and exit
ordering. Results are sorted by scenario ID. An explicit baseline must name a
supplied scenario; deltas are scenario minus baseline. Undefined expectancies
produce null expectancy deltas. No baseline or scenario grid is invented.

## Validation and scope

Focused coverage: `tests/test_m7_1_replay_performance.py`. Regression gates also
include legacy M7, M6.1, legacy M6, downstream M8, full pytest, whitespace and
static safety/I/O checks, and verification that tracked legacy files are unchanged.

Only in-memory contracts and arithmetic are added. No network/provider calls,
DB/schema changes, services, broker integration, or execution adapters are added.
US7C-B mapping and corporate-action execution review belong to the subsequent
preflight phase, which must wait for this phase's manual commit.

### Phase 2 verification snapshot

Validated on branch `agent/us-market-foundation`, base HEAD
`2afe1c8a9be199bbc0c0b9c5d50e757082ab4b2a`. Python commands used
`PYTHONDONTWRITEBYTECODE=1` with the authorized shared offline interpreter,
executed from the US workspace. No environment installation or modification
was performed.

| Gate | Exact result |
| --- | --- |
| `py_compile` for both new modules and the new test file | exit 0 |
| `tests/test_m7_1_replay_performance.py` | 84 passed |
| Legacy `test_m7_performance.py`, `test_m7_extensions.py`, `test_m7_performance_ui.py` | 78 passed |
| `test_m6_1_paper_replay.py` and `test_m6_paper_execution.py` | 142 passed (38 M6.1 + 104 legacy M6) |
| `test_m8_research.py` and `test_m8b_evaluation.py` | 181 passed |
| Full `pytest -q` | 1587 passed in 23.16s |
| `git diff --check` and no-index whitespace checks of additions | clean |
| Static import/call scan | no prohibited I/O; test `request()` matches are the local in-memory fixture |
| Legacy implementation/test diff against base HEAD | unchanged |

An initial focused test incorrectly assumed exactly one internal recomputation
call; Pydantic may rerun an after-validator when nesting a reconstructed model.
The corrected assertion requires fresh recomputation of the same canonical
input without assuming an internal call count. Final gate runs had no failures
or warning summaries.

No files were staged or committed. Proposed manual commit:
`feat: add multi-session paper performance`. Stage only the two new
`app/performance/replay*.py` modules, the new focused test file, and this document.
