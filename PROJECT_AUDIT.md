# One-time project audit

Audit date: 2026-09-26. Source revision: `829676d` on
`agent/er1c-free-acquisition`. This is the M1 audit for the autonomous EGX + US
mission, not the older M1–M8 research-engine milestones. Preserve this audit as
reference; continue from `PROGRESS.json` and the relevant capability-map row.

## Scope and evidence limits

Reviewed the project-owned architecture, entry points, contracts, critical
admission/strategy/risk paths, test coverage, deployment configuration, and
relevant milestone documentation/history. The inventory below covers all
project-owned Python modules; it is an architectural audit, not a claim of a
line-by-line proof. Dependencies, caches, generated logs, Git internals, the old
repository and production paths were excluded. No provider investigation was
reopened. No external source, rights, or current market fact was newly certified.

Evidence grades used here:

- Source inspection establishes implemented behavior and wiring.
- Existing regression tests and prior recorded results support component
  engineering status; they do not establish market validation.
- Read-only isolated runtime observation establishes only the explicitly
  timestamped state below, not production health or full-universe coverage.

The current environment has Python 3 but no pytest, FastAPI or Pydantic in its
system interpreter. An isolated `/tmp/egx-mission-venv` was created; dependency
installation failed because package-host DNS was unavailable. The current full
suite was **not run**. Historical launch documentation records 2,770 passing
tests before subsequent fixes; that number is not a current test result.
`BASELINE.md` reports the starting baseline as validated. Source parsing and the
stdlib-only operational reader can still be checked offline.

## Architecture and dependency flow

The application is a Python/FastAPI monolith with SQLite schema version 9,
immutable raw/canonical artifacts, strict typed contracts, and separately
composed workers/CLIs. `app/main.py` is the active API. `app/experimental.py` and
the experimental snapshot/runtime tools are compatibility infrastructure.

Data flows through provider response -> immutable raw receipt -> ingestion
ledger -> semantic normalization -> canonical artifact/source linkage -> PIT
reference and review admission -> strategy. Paper candidate admission,
watchlist publication, triggers, fills, positions, exits, portfolio accounting
and performance have distinct contracts. The separation should be retained.

`app/storage/database.py` owns schema initialization/version guards; repositories
separate reference artifacts, security identity, scheduler jobs, holiday and
session transitions, canonical artifacts and trade/audit records. Changes must
preserve raw hashes, receipt times, source identity and existing migration tests.

## Actual coverage observed

Read-only observation at `2026-09-26T19:40:20.181437+00:00` of the explicitly
authorized isolated runtime
`/home/egx-agent/research-data/paper-shadow-operational/platform.db`:

- Security master: 319 records, comprising 312 EQUITY and 7 INDEX records from
  `egid`; all source market dates are null. This is not an authoritative current
  universe, not 319 equities, and not evidence that the 224 EGX target changed.
- Two canonical daily artifact records, one distinct symbol: COMI. Artifact
  count is not symbol count or scan count.
- One `PAPER_SIGNAL_VERIFIED` receipt, accepted by the existing operational
  reader's digest/PIT-reference/symbol/time checks: COMI, READY_NO_SIGNAL,
  decision `2026-09-26T15:30:03.798654+00:00`, session `2026-09-24`, 260 bars,
  source `tradingview_tvdatafeed_egx`, expires `2026-09-27T07:00:00+00:00`.
- Therefore one observed verified/data-ready/scanned EGX symbol, zero WATCH,
  one READY_NO_SIGNAL at that observation. This is a bounded receipt snapshot,
  not a new strategy run or a statement of ongoing freshness. After expiry it
  must no longer contribute to current ready/scanned counts.
- Remaining target symbols have not been individually classified. Do not report
  223 EVIDENCE_BLOCKED or NOT_READY by subtraction.
- Zero scheduled-job rows in this isolated database. This says nothing about
  another scheduler or a running process.
- No configured operational U.S. universe was found in the repository. IBM/TWTR
  in historical pilot documentation are research cases, not a product universe.
  U.S. universe, data-ready, scanned and candidate counts remain unknown.

## Data, provenance and market separation

`app/data` implements raw storage, ingestion, quality, security-master aliases,
daily/index normalization, canonical stores, source ledgers, quota admission,
and PIT loading. PIT checks bind actual raw artifacts to source reviews, dated
eligibility, bounded corporate actions and availability at the decision cutoff.
Explicit-symbol selection and operational history-window handling were fixed in
`712a72a`, `0d6e99d`, and `6617c3b`; preserve those distinctions rather than
silently claiming historical-universe completeness for an explicit selection.

Daily admission requires 260 valid bars and rejects stale/duplicate history.
Split-adjusted observations are used for indicators; raw Decimal prices remain
planning references. Reviewed non-split adjustment effects are explicit; unknown
effects must block. Current aliases cannot establish historical identities.

Provider implementations include EGID, EODHD and official EGX index acquisition.
The default daily-refresh runtime still composes EODHD and five targets (COMI,
EAST, FWRY, ORAS, SWDY). EGID can require authentication/subscription. Neither
default is proof of a zero-paid autonomous pipeline. The working COMI receipt
binds TradingView data, but the repository has no corresponding reusable
TradingView acquisition adapter or automatic free fallback chain. Existing
external acquisition artifacts are not automatically production integrations.
Provider availability, rights, rate limits and coverage must retain separate
statuses; an anonymous response cannot establish authorized usage.

`app/core` has EGX schedule/session orchestration, base calendar, reviewed holiday
evidence, trading-day promotion, maintenance/live verification and dispatch
contracts. Unknown mandatory calendar truth blocks dispatch. Do not replace
evidence with weekday assumptions. `app/us` separately models listing MIC,
America/New_York dates, UTC clocks, exact-date universe membership, daily and
intraday facts, split/action history and retrospective PIT admission. Preserve
these U.S. controls; do not route U.S. symbols through EGX calendar assumptions.

## Strategy and trading-logic audit

All strategy outputs remain unvalidated research/paper observations. Engineering
fixtures do not establish expectancy, profitability or operational suitability.

| Engine | Purpose and setup | Confirmation/invalidation | Plan, risk and operational boundary |
| --- | --- | --- | --- |
| SWING v1 | Trend/breakout baseline on admitted split-adjusted daily history; close > fast EMA > slow EMA, optional close above prior highs | Insufficient/PIT-invalid data rejects; absent pattern returns NO_CONFIRMATION, mapped to READY_NO_SIGNAL in launch | Operational config 50/20/50/20; next eligible session only; raw Decimal close; Q03 entry ±0.5%, stop −3%, target +6%. Fixed percentages are frozen assumptions, not volatility/structural validation. Unsized WATCH, liquidity/risk not evaluated. |
| PRE_SURGE v7 | Legacy V5 scorer-row parity, not a trained V7 model | Available, same-source/cohort/model rows; training before signal; return <5% eligibility | Percentile score weights 1/.25/.15/.05 minus stop-risk penalty. Research outcome ranking only; does not establish a valid operational setup, executable plan or trained probabilities. Must not become operational rank-to-candidate conversion. |
| FIRST15 | Exact opening-window return and close-location with supplied prior bias | Explicit opening/return-floor invalidation; UP/DOWN bias gives continuation/reversal; NONE no confirmation | No executable plan or sizing. Offline continuous-bar research only. |
| ORB | Later close above buffered opening-range high | Optional relative-volume threshold; incomplete opening/later bar gives no confirmation | Optional range-low stop; entry level is a reference, not fill; no operational target/risk pipeline. |
| VWAP pullback | Extension, later VWAP-band intersection, still-later reclaim | Uses actual cumulative traded value/volume, no typical-price replacement | Optional trailing-low stop; no automatic fills or targets. |
| Momentum continuation | Lookback return with optional relative volume and maximum VWAP extension | Requires admitted continuous history and configured gates | Optional trailing-low stop; offline research only. |
| U.S. SWING | Same pure SWING math through U.S.-specific admitted PIT adapter | Revalidates listing/session/actions/universe and stable identity | U.S. research plan/risk/replay wrappers preserve USD, MIC and provenance; not wired to an unattended operational scanner. |

The manual EGX `SwingLaunchInput` hardcodes five accepted symbols. This is a real
coverage limit to remove through tested identity/universe-driven admission, not
by bypassing evidence. No full-universe scan coordinator, durable per-symbol scan
run accounting, or operational candidate ranking was found.

EMA, breakout, return, relative volume and cumulative VWAP exist for the stated
purposes. There is no verified operational relative-strength/benchmark/sector
pipeline, market-regime producer or volatility-aware stop/sizing integration.
Risk consumes explicit regime and group inputs; their existence as fields does
not establish measured regime/correlation evidence. These are missing inputs,
not reasons to add redundant indicators now.

## Risk, lifecycle, performance and research

`app/risk` revalidates plans/context, decision time, lifecycle state, regime,
daily loss, concurrency and reward/risk. Whole-share sizing takes the minimum
of risk budget, position, portfolio exposure, cash, portfolio open risk, symbol,
optional group and liquidity caps. Unknown regime blocks. Snapshot authenticity,
freshness, group membership and serialized capital reservations are upstream
obligations. A statistical correlation model or complete sector allocator does
not follow from the group cap.

`app/paper` includes deterministic OHLC simulation and replay, plus independently
audited forward collection/fact/trigger/fill/position/exit/continuation records.
The forward path binds evidenced costs, slippage, participation and market
currency. Entry/exit chronology, pessimistic ambiguous-bar behavior, actual
fact availability and missing-data semantics are important preserved controls.
Portfolio reservations, settlements, native cash, marks, snapshots and daily
series exist. Automatic operation for both markets remains unproven.

`app/performance` aggregates canonical execution/replay observations, including
costs, net expectancy, drawdown, MAE/MFE, R and sample limitations. The active
`/performance` route calls the renderer with no report and correctly displays
unavailable. Audited NAV/valuation series and return bridges are distinct from
trade-performance observations; never manufacture a report from candidates or
NAV alone.

`app/research` and U.S. validation modules implement label availability, purged
walk-forward partitions, development OOS, frozen holdout, bootstrap uncertainty,
cost/scenario sensitivity and evidence criteria. Historical public-source pilot
findings still record missing admitted evidence. No real empirical validation
was established by this audit; preserve the closed investigations and do not
promote research engines on fixture results.

## API, operator visibility, scheduler and deployment

Active routes: `/`, `/api/today`, `/api/paper-operational`, `/shadow`,
`/api/shadow`, `/performance`, `/health`. Configured operational reads take
precedence over legacy artifact display, with no silent fallback on failure.
The operational reader validates receipts, expires them and suppresses stale
plans; it only discovers symbols with canonical artifacts, not the full universe.
Stock reporting has source/session/window/state and conditional plan levels;
complete confirmations, invalidations and negative evidence are not yet exposed.

SYSTEM and several requested tabs are navigation labels rather than active
product routes. No unified EGX/US/ALL operational view or deployed-revision,
project checkpoint, scheduler-heartbeat/provider-health dashboard exists in the
active application. `/health` reports process health, not data or scheduler
readiness. M2 is the next useful integration task.

The worker polls EGX calendar/checkpoint truth with durable job claims/recovery;
its default is OBSERVE, and PAPER_REFRESH composes token-based daily refresh.
It does not schedule a complete EGX + US strategy/lifecycle pipeline. Compose
declares observe mode and a production secret mount; it was inspected as source
only and was not run. Docker builds only requirements and `app`, so future
checkpoint/build metadata must be explicitly packaged or mounted safely.

Baseline deployment is operator-reported, not reverified here. No safe deployment
mechanism was found in the repository; production paths/socket remain untouched.
Deployment pending is a capability limitation, not a whole-mission blocker.

## Tests, history and priorities

Tests cover providers/normalization/PIT, calendars and scheduler ledgers, strategy
contracts, risk, replay, lifecycle tampering/chronology, API read-only behavior,
portfolio/valuation ancestry, U.S. evidence and research validation. The autouse
socket guard makes these offline engineering tests, never real market evidence.
Current dependencies must be provisioned in the active workspace before claiming
a green current suite. Do not borrow the forbidden old repository's virtualenv.

Recent commits preserve operational UI binding (`e55fbbc`), window/source/PIT fixes
(`6617c3b`, `0d6e99d`, `712a72a`), and manual orchestration (`653b73d`). Older
`AGENT_STATUS.md`, `EXECUTION_PLAN.md` and documentation use obsolete branch,
mission and approval language. The current mission and `AGENTS.md` take priority.
`MISSION.txt` was absent at audit start; `PROGRESS.json` is the continuation entry.

Next sequence: M2 read-only SYSTEM progress and provenance-aware observed counts;
M3 authoritative dated universe and reusable free acquisition/admission; M4
universe-driven per-symbol scan accounting and scheduling; M5 valid-only ranking
and explanations; M6 U.S. operational acquisition/scanning; M7 authentic lifecycle
and performance integration; M8 scheduled reliability and release verification.
Do not reopen completed research-engine work under similarly numbered milestones.

LIVE_MONEY=DISABLED. No order, provider acquisition, production mutation, evidence
creation, simulated market result or new operational signal occurred in this audit.

## Project-owned module inventory

All 295 Python source/test files parsed successfully with the current Python 3 interpreter. Parsing proves syntax only, not imports or behavior. Each implementation group below was mapped to its role above and its matching tests.

- `app`: `__init__.py`, `experimental.py`, `main.py`.
- `app/api`: `__init__.py`.
- `app/core`: `__init__.py`, `base_calendar_service.py`, `base_trading_calendar.py`, `calendar_evidence.py`, `calendar_live_dispatcher.py`, `calendar_live_execution.py`, `calendar_live_runtime.py`, `calendar_maintenance_dispatcher.py`, `calendar_maintenance_execution.py`, `calendar_maintenance_job.py`, `calendar_maintenance_runtime.py`, `calendar_truth.py`, `calendar_verification.py`, `calendar_verification_service.py`, `config.py`, `daily_refresh_dispatcher.py`, `daily_refresh_execution.py`, `daily_refresh_runtime.py`, `holiday_promotion.py`, `holiday_promotion_service.py`, `holiday_verification.py`, `holiday_verification_service.py`, `job_state.py`, `orchestrator.py`, `runtime_secrets.py`, `schedule.py`, `scheduler_execution_context.py`, `scheduler_worker.py`, `trading_day_promotion.py`.
- `app/data`: `__init__.py`, `daily_canonical.py`, `daily_canonical_pipeline.py`, `daily_canonical_store.py`, `daily_ingestion.py`, `daily_refresh_admission.py`, `daily_refresh_job.py`, `index_canonical.py`, `index_canonical_pipeline.py`, `index_canonical_store.py`, `index_ingestion.py`, `ingestion_repository.py`, `intraday.py`, `models.py`, `official_index_refresh_job.py`, `official_index_refresh_runtime.py`, `point_in_time.py`, `provider.py`, `quality.py`, `quota.py`, `raw_store.py`, `reference.py`, `security_master.py`, `validated_index_repository.py`.
- `app/data/providers`: `__init__.py`, `egid.py`, `egx_official.py`, `eodhd.py`.
- `app/domain`: `__init__.py`, `enums.py`, `models.py`.
- `app/paper`: `__init__.py`, `models.py`, `replay.py`, `replay_models.py`, `shadow_allocations.py`, `shadow_candidate_admission.py`, `shadow_collection.py`, `shadow_continuations.py`, `shadow_daily_portfolio.py`, `shadow_daily_series.py`, `shadow_daily_snapshots.py`, `shadow_exits.py`, `shadow_facts.py`, `shadow_fills.py`, `shadow_freeze.py`, `shadow_ledger.py`, `shadow_performance_bridge.py`, `shadow_portfolio.py`, `shadow_positions.py`, `shadow_producer.py`, `shadow_records.py`, `shadow_report.py`, `shadow_security_type.py`, `shadow_triggers.py`, `simulator.py`, `swing_launch.py`.
- `app/performance`: `__init__.py`, `analyzer.py`, `extensions.py`, `models.py`, `replay.py`, `replay_models.py`.
- `app/research`: `__init__.py`, `evaluation.py`, `evidence.py`, `historical_evidence.py`, `historical_pit.py`, `models.py`, `splits.py`.
- `app/risk`: `__init__.py`, `engine.py`, `models.py`, `policy.py`.
- `app/storage`: `__init__.py`, `canonical_artifact_repository.py`, `daily_canonical_artifact_repository.py`, `database.py`, `holiday_evidence_repository.py`, `market_session_transition_repository.py`, `reference_repository.py`, `repository.py`, `scheduler_repository.py`, `security_master_repository.py`.
- `app/strategies`: `__init__.py`, `contracts.py`, `eod.py`, `intraday.py`.
- `app/ui`: `__init__.py`, `operational.py`, `performance.py`, `shadow.py`, `shadow_input.py`, `today.py`.
- `app/us`: `__init__.py`, `contracts.py`, `historical_actions.py`, `historical_daily.py`, `historical_identity.py`, `historical_intraday.py`, `historical_session.py`, `historical_universe.py`, `paper_replay.py`, `paper_replay_models.py`, `research_adapter.py`, `research_planning.py`, `research_validation.py`, `research_validation_models.py`, `retrospective_pit.py`.
- `tools`: `audit_er1c_forward_session_truth.py`, `audit_er1c_nasdaq_halt_fields.py`, `audit_er1c_nasdaq_halt_terms.py`, `audit_er1c_nyse_corporate_actions_product.py`, `audit_er1c_nyse_corporate_actions_scope.py`, `audit_er1c_nyse_roster_probe.py`, `audit_er1c_nyse_symbol_mapping_spec.py`, `audit_er1c_sec_direct_submission_attempt.py`, `audit_er1c_sec_form_sample_declaration.py`, `audit_er1c_sec_listing_ledger_probe.py`, `audit_er1c_sec_twitter_submission_probe.py`, `audit_er1c_us_pilot.py`, `experimental_runtime.py`, `experimental_snapshot.py`, `paper_shadow_launch.py`, `produce_shadow_watchlist.py`.

Test inventory: 137 Python files, including the offline network guard. No dependencies or generated artifacts are included.
