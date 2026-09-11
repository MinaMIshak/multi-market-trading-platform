# M4 milestone status

Result: **M4 PASS**

"Engineering complete" does not mean "strategy validated".

## Scope and HEAD

- Starting HEAD: `48e087e940c2360ed1580567dbc5591c9b0b3ef1` (required `48e087e`).
- Ending HEAD: `48e087e940c2360ed1580567dbc5591c9b0b3ef1`.
- Branch: `agent/development`. No staging or commits performed.
- AGENTS.md and EXECUTION_PLAN.md read completely and left unchanged.
- M4 only. STOPPED before M5. No risk sizing or simulator implementation.

## Architecture and files changed

All seven files are new, uncommitted workspace files:

- `app/strategies/contracts.py`: strict immutable versioned candidate boundary,
  explicit config identity, evidence JSON, LONG/UNVALIDATED/execution-disabled outputs.
- `app/data/intraday.py`: offline validated continuous-session bars, explicit sequence
  origin, contiguous intervals, provenance consistency and point-in-time consumption.
- `app/strategies/eod.py`: M3-only Swing EMA/breakout research baseline and
  PreSurgeV7Engine LEGACY_V5_PARITY_SEED orchestration/ranking, without training.
- `app/strategies/intraday.py`: First15, ORB, VWAP pullback and momentum research
  engines with required typed configs; First15 contextual bias is also point-in-time.
- `tests/test_m4_engines.py`: 102 synthetic acceptance cases, including real isolated
  M3 repository integration for Swing and future-data replay tests.
- `docs/M4_TRADING_ENGINES.md`: parity facts, authorized contracts, assumptions,
  parameter limits and M8 requirements.
- `AGENT_STATUS.md`: this report.

Existing architecture, scheduler, quota, calendar, provider, storage and risk code
remain unchanged. No engine is wired to any operational job or execution path.

## Hardening changes

Candidate identifiers require non-empty strings; optional entry/stop/target
references must be positive. candidate() requires an explicit strategy_version:
Swing and all four intraday engines pass 1; PRE_SURGE passes 7. Evidence serialization
accepts ISO dates/datetimes and fails closed on unknown objects; no default=str.
First15 qualification has no hidden positive-return rule. Present traded_value
must be zero exactly when volume is zero; VWAP-required paths still reject missing
value. Swing retains decision_time as its actual M3 as_of cutoff, documented as a
knowledge/query cutoff rather than a fabricated source timestamp.

## Schema impact

None. No schema code or production migration. Swing retains the existing M3
repository's audit/DQ behavior; integration tests use pytest temporary databases
and raw artifacts only. Engines create no trades or fills.

## Validation

- Focused command: `.venv/bin/python -m pytest -q tests/test_m4_engines.py`
  Final exact result: **102 passed in 1.02s**.
- Full command: `.venv/bin/python -m pytest -q`
  Final exact result: **522 passed in 15.57s** (420 existing + 102 M4).
- This hardening review's focused and full runs both passed on the first run.
- Added regressions for non-empty candidate identifiers, positive optional references,
  execution rejection, required explicit versions for every engine, deterministic ISO
  date/datetime evidence and rejection of unknown objects, explicit First15 return
  gates, traded-value consistency, and non-VWAP missing-value acceptance.
- Swing integration observes repository.load(as_of=decision_time) and asserts the
  candidate cutoff equals that query cutoff. Existing future receipt, future bar,
  future bias, training leakage and future cohort protections continue to pass.
- `git diff --check`: clean. New untracked files additionally checked with
  `git diff --no-index --check` against an empty workspace file; no whitespace errors.

- Reviewed implementation and tests; tracked `git diff` is empty because additions
  remain untracked as requested. Protected-file diff and HEAD checks are clean.
- Existing autouse network prohibition remains unchanged; every test runs with
  socket connection functions blocked. No network failures occurred.

## Safety accounting

Network / external provider / EODHD / production / secrets / Docker attempts = **0**.
No sudo, paid API, API keys, deployment, production database, broker calls,
operational paper_refresh, model training, or live-money execution.
No AGENTS.md/EXECUTION_PLAN.md changes. No git add/commit.

## Unvalidated assumptions and remaining risks

- All results remain research/paper candidates: UNVALIDATED and execution_allowed=False.
  No positive expectancy or operational-readiness claim is made.
- Swing uses only the minimal explicit EMA and optional breakout configuration;
  RSI/ADX/liquidity/volatility/reward-risk gates are not enabled or guessed.
  Its D-close reference is planning-only for NEXT_ELIGIBLE_SESSION; verified calendar
  resolution and executable pricing belong to later consumers. No next-open is read.
- Historical V5 parity facts come from the mission specification, not a newly
  authenticated historical artifact. Risk penalty is explicitly supplied.
  V7 is orchestration, not a newly trained model or executable-profitability model.
- Intraday origin, source identity, traded-value semantics and contextual bias are
  trusted offline importer attestations. This work does not establish real provider
  authority, complete real datasets, or an operational ingestion adapter.
- Continuous data gaps reject conservatively. First15 requires exact coverage and
  cannot split a bar across the window boundary. No exchange hours are assumed.
- Pattern definitions, all configured thresholds and candidate ranking remain
  unvalidated. M8 requires executable labels, costs/slippage, purged walk-forward,
  frozen holdout, regimes, uncertainty and out-of-sample expectancy/drawdown analysis.
- No engineering blocker remains within the authorized M4 scope.

Recommended next milestone: human review of M4, then separately authorized
**M5 – Risk and Portfolio Engine**. M5 has not been started.
