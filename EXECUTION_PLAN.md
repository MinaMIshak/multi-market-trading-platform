# EGX Trading Platform - Execution Plan

## Objective
Reach a safe, measurable Paper Trading MVP for EGX.
Follow milestones in order. Do not skip safety gates.
Live-money execution is outside this plan.

## Starting Baseline
Working branch: agent/development
Starting commit: c2c349783229df4fb3a4b2dfa8020ce344aa5574
Known baseline regression: 333 tests passed before agent handoff.

Production is NOT this workspace.
The agent must never attempt to inspect or synchronize with production.

## M0 - Baseline Reproduction
Goal: prove this isolated workspace can reproduce the project safely.

Required:
- inspect repository structure and existing tests
- run git diff --check
- run the full existing regression
- verify tests use non-production/temp data
- document baseline failures without weakening tests

Exit gate:
- clean reproducible baseline, or STOP with blocker report

## M1 - Calendar/Scheduler Candidate Closure
Goal: finish validation of the code already present at c2c3497.

Required:
- validate calendar maintenance disabled by default
- validate local mode in observe mode
- validate two-poll semantics
- ensure calendar maintenance never triggers Daily Refresh
- verify unsupported mode fails closed
- use isolated/temp DB only
- use rootless Docker only
- no EODHD calls
- no production deployment

Exit gate:
- focused tests pass
- full regression passes
- candidate behavior documented

## M2 - Market Data Quota Guard
Goal: prevent uncontrolled provider usage before paper_refresh exists.

Required:
- explicit request budget accounting
- configurable daily base budget
- reserve budget
- extra-credit auto-consumption disabled
- bounded retries
- no repeated polling pattern
- pre-run estimated cost admission
- fail closed when budget is unavailable
- tests must not consume real provider quota

Initial policy target:
automatic base usage <= 15 units/day
reserve >= 5 units/day

Do not assume every endpoint costs one unit.

Exit gate:
paper_refresh cannot execute without passing quota admission.

## M3 - Data Foundation Completion
Goal: reliable point-in-time market inputs.

Required:
- point-in-time eligible EGX universe
- provenance for every artifact
- immutable raw layer
- canonical validation
- DQ status
- corporate-action handling
- stale-data rejection
- deterministic/replayable ingestion
- no survivorship shortcuts

Preferred authoritative source:
official EGX/EGID when technically available.

Secondary providers require validation before promotion.

## M4 - Trading Engines V1
Implement only after required data contracts exist.

EOD:
- Swing engine
- Pre-Surge V7

Intraday V1:
- Opening Range Breakout
- VWAP Pullback
- Momentum Continuation

Rules:
- executable entries, not hindsight entries
- no look-ahead
- setups produce structured evidence
- no synthetic recommendations

## M5 - Risk and Portfolio Engine
Required before simulated execution.

Include:
- position sizing
- per-trade risk
- portfolio exposure
- cash constraints
- correlated exposure controls
- stop/target policy
- daily risk limits
- invalidation logic

Trade lifecycle:
SCANNING -> WATCH -> SETUP_FOUND -> READY ->
ENTRY_TRIGGERED -> IN_POSITION -> T1_HIT ->
TRAILING -> EXIT

Alternative terminal state:
INVALIDATED

## M6 - Paper Execution Simulator
Goal: executable-trade simulation, not signal counting.

Model:
- next executable price
- fills/no-fills
- transaction costs
- slippage
- gaps
- stop/target sequencing
- partial state where required
- MAE/MFE
- R multiple
- net P&L

Outcome states include:
WIN
LOSS
TIME_EXIT
NO_FILL

## M7 - Performance and User Surface
Performance must report at least:
- net expectancy
- profit factor
- max drawdown
- average win/loss
- Sharpe/Sortino where valid
- MAE/MFE
- slippage sensitivity
- monthly/regime consistency

UI target:
TODAY | LIVE | PRE-SURGE | SWING |
PERFORMANCE | RESEARCH | SYSTEM

Do not display fake setups when no real engine exists.

## M8 - Research Validation
Required before any recommendation for real-money readiness.

Use:
- executable labels
- purged rolling walk-forward
- frozen future holdout
- costs/slippage
- regime analysis
- bootstrap/confidence analysis
- out-of-sample reporting

Optimization objective:
robust net expectancy and controlled drawdown,
not maximum hit rate.

ML is explicitly deferred until deterministic baselines are validated.

## Global Definition of Done
For every milestone:
- focused tests pass
- full regression passes when appropriate
- git diff --check passes
- safety behavior is tested
- no hidden external API usage
- no production access
- one logical commit per coherent change
- milestone report is produced

## Current Execution Rule
Start at M0.
Then complete M1.
Do not begin M2 until M0 and M1 are demonstrably complete.

After M1, STOP and produce a milestone report for human review.
