# Existing-platform lifecycle delivery — 2026-09-19

## Repository recovery and scope

Requested starting HEAD: `97b918cb1355dd0f6237a92edcf3bdac7e465c17`.
Actual starting HEAD: `edc22995c3f5a7f18a951b70cb82a3718b9fefe4`.
Branch: `agent/er1c-free-acquisition`. Initial worktree: clean.

The supervisor had already committed `edc2299 feat: connect explicit shadow
collection to existing platform UI` before this invocation. Its producer,
independent portfolio read paths and UI improvements were preserved, not rebuilt.
The current mission supersedes AGENTS.md's older workspace, development branch,
direct-commit instruction and EXECUTION_PLAN.md's historical M1 stop point.

Before implementation: read AGENTS.md and EXECUTION_PLAN.md fully; inspected the
latest 110 commits and path-specific history; read current recovery and sprint
audits and relevant M3–M8, R1, ER1/ER1B/ER1C and forward-protocol documentation;
inspected data/PIT, strategies, paper, performance, UI/main, calendar-live and
scheduler implementation and tests. Historical documents are cumulative milestone
records, not an instruction to redo subsequently committed work. Initial full
regression: **2679 passed in 171.25s (0:02:51)**.

| Material area | Reconciled classification | Current repository evidence |
| --- | --- | --- |
| M0–M2 | ALREADY_DONE | Offline regression guard, scheduler/calendar contracts and quota admission |
| M3 | ALREADY_DONE software; EVIDENCE_BLOCKED coverage | Immutable/canonical storage, reviewed dated universe/actions, stale/PIT rejection |
| M4 | ALREADY_DONE research engines | Swing/Pre-Surge/intraday implementations; UNVALIDATED and execution_allowed=False |
| Explicit M4-to-Shadow production | ALREADY_DONE | 97b918c admission plus edc2299 producer/CLI, completion, candidate event and UI envelope |
| M5/M6 | ALREADY_DONE engineering | Explicit risk policies and caps, paper simulator/replay, separate Shadow allocation and event audit |
| Early Shadow lifecycle UI | REAL_SOFTWARE_GAP, closed here | Existing trigger/fill/position readers were unreachable through execution.json without exit evidence |
| Pre-settlement reservation UI | REAL_SOFTWARE_GAP, closed here | Existing reservation auditor was not exposed before settlement or valuation |
| Exit/continuation/settlement UI | ALREADY_DONE | Existing strict transport calls complete upstream auditors |
| Portfolio snapshots and valuation series | ALREADY_DONE | Independently audited dated native-currency snapshots and actual interval series |
| M7 | ALREADY_DONE software; EVIDENCE_BLOCKED operational source | Legacy M7 requires canonical M6 input/result and independently reruns simulation; M7.1 preserves replay evidence separately; Shadow/NAV cannot be converted into canonical M6 evidence |
| M8 and US8 | ALREADY_DONE engineering; EVIDENCE_BLOCKED validation | Purged/OOS/frozen-holdout evaluation exists, authentic admitted sample does not |
| R1/ER1/ER1B/ER1C | ALREADY_DONE qualification/contracts; EVIDENCE_BLOCKED admission | Closed source paths remain NO_GO; original-byte availability/review/universe/session/action gaps remain |
| Calendar/index/TODAY | ALREADY_DONE | Fail-closed retained official-index verification, gated refresh pipeline, persisted exact-date session rendering |
| Stock/ETF/cash intelligence | PARTIALLY_DONE | Native cash/positions exist; no Shadow security-type binding or ETF comparison adapter |
| IBKR paper economics | PARTIALLY_DONE | Audited generic costs/slippage/participation exist; no verified date-effective IBKR schedule binding |
| Multiple horizons | PARTIALLY_DONE | Actual intraday, continuation and valuation intervals exist; validated predictive horizons do not |
| Separate Experimental product/runtime | OBSOLETE_OR_SUPERSEDED | No implementation or runtime changes in this delivery |
| Live execution, automatic promotion and ungated refresh | DELIBERATELY_DISABLED | No changed execution authorization, provider/scheduler configuration or safety gates |

## One coherent milestone: expose audited lifecycle stages

The existing `/shadow` and `/api/shadow` routes can now consume four additional
explicit schemas from the same bounded `execution.json` file. All require the
separately audited candidate collection in `input.json`.

| schema_version | Exact additional envelope fields | Audit boundary |
| --- | --- | --- |
| shadow-ui-trigger-v1 | facts, fact_packages | trigger_evaluation_view |
| shadow-ui-entry-fill-v1 | facts, fact_packages, fill_policy, fill_packages | entry_fill_view |
| shadow-ui-position-open-v1 | facts, fact_packages, fill_policy, fill_packages | position_open_view |
| shadow-ui-reservation-v1 | facts, fact_packages, fill_policy, fill_packages, portfolio | capital_reservation_view / audit_capital_reservation |

Every envelope also contains `schema_version`. Models contain every canonical
field, including explicit nulls/defaults; tuples are JSON arrays, Decimals are
strings and clocks explicitly express UTC. The existing exact-field, duplicate-key,
4 MiB regular-file and canonical-model checks remain. An operator selects one
stage explicitly. There is no inferred "latest" stage, no fallback from a missing
or damaged later receipt, no receipt creation during reads and no historical
reconstruction. Existing exit, continuation and settlement schemas remain intact.

The UI preserves triggered, not-triggered, invalidated, target-passed, ambiguous,
capacity-rejected and filled outcomes. A fill does not imply a position. A position
receipt means simulated open at entry; current state remains unknown without its
exit audit. A reservation means a historical allocation decision, even if the
position has since settled. Its stop-cost risk estimate is not a bound on gap or
slippage losses. Historical cumulative reservation totals are deliberately not
presented as currently active capital or buying power.

Entry economic policy appears only after its audit; an unavailable exit policy
remains null. No trigger-only record acquires an invented fee policy. IBKR
applicability remains NOT ESTABLISHED. The reservation surface exposes its exact
frozen portfolio policy and receipt references, not a new risk decision or a new
portfolio calculation. All outputs remain EXPERIMENTAL / PAPER ONLY, NOT SCORED.

## Product surfaces and their truth sources

- TODAY data: consistent read-only canonical SQLite inventory; structural validity
  is not freshness or historical universe eligibility.
- TODAY market/session: exact-date persisted MarketSession with payload/column
  consistency; missing truth remains UNVERIFIED.
- TODAY/Shadow candidates: explicit admission, frozen completion, candidate ledger
  and reviewed evidence packages. M4 planning floats are never executable geometry.
- Shadow trigger/fill/position/reservation: explicit execution schema plus the
  matching durable event and its full existing upstream audit. No downstream
  event, P&L, current position or NAV is inferred.
- Shadow exits/continuations/settlements: authenticated facts, policy and complete
  event ancestry; CLOSED alone is not settlement; UNKNOWN remains UNKNOWN.
- Portfolio/NAV: dated immutable snapshot, frozen complete ledger and authenticated
  marks for every active reservation; native gross valuation, not liquidation NAV.
- Valuation intervals: independently audited actual dated snapshots; gaps remain
  gaps and do not become synthetic daily M7 observations.
- PERFORMANCE: canonical M7 contracts exist, but no authentic operational source
  is attached; the route remains unavailable.
- Stock/ETF/cash, economics and horizons: actual native cash and position data,
  audited policy assumptions and observed intervals only. No ETF comparison,
  broker quote, validated probability or predictive holding horizon is invented.

## Verification and safety

All tests use artificial isolated files and the repository's socket-denial fixture.
No authentic market evidence, candidate, fill, result or missed session was created.
No source acquisition, license/terms probing or external API/network calls occurred.
No production paths, secrets, services, databases, ports, scheduler processes,
brokers, Docker or deployments were accessed or changed. No git staging or commits
were run by Codex. The existing shared virtual environment was executed read-only.

Exact command prefix for every pytest run:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python -m pytest -q
```

Commands/results (append the listed test paths to that prefix):

- Initial full suite, no appended paths: **2679 passed in 171.25s (0:02:51)**.
- `tests/test_main_shadow_entry_ui.py tests/test_main_shadow_execution_ui.py`:
  **55 passed in 10.24s** (before reservation integration).
- `tests/test_main_shadow_entry_ui.py tests/test_shadow_report.py tests/test_shadow_allocations.py tests/test_main_shadow_execution_ui.py tests/test_main_shadow_settlement_ui.py`:
  **145 passed in 23.18s**.
- Final missed-collection integration regression:
  **133 passed in 9.41s**.
- Final full repository suite after all lifecycle and missed-session changes:
  **2723 passed in 169.63s (0:02:49)**.

New tests cover read-only success, missing durable stages, tampered ancestry,
rehashed false reservation economics, altered policy/evidence, unapproved review,
extra/missing/duplicate/mixed envelope fields, links, independent candidate
availability, current-state non-inference and historical reservation semantics
after settlement. The final integration test additionally covers authenticated
`missed.json` transport through `main.shadow()`, `/shadow` and TODAY, verifies that
MISSED / NOT SCORED is distinct from a zero-candidate watchlist, rejects a tampered
missed receipt, and proves reads do not reconstruct candidates or execution state.
Existing admission and safety tests are not weakened.

Final `git diff --check`: **PASS**. No files were staged during validation.

## Remaining work and delivery status

EVIDENCE_BLOCKED: authentic timely forward inputs, exact historical identity and
universe, complete every-date sessions and bounded actions, historical edition
availability and approved independent review, authentic execution bars and dated
costs, actual canonical M7 observations, OOS/untouched holdout and forward samples.
Stock/ETF comparisons, IBKR applicability, FX and validated predictive horizons
also lack necessary evidence. Closed free-source paths must not be re-probed.

DELIBERATELY_DISABLED: real-money execution, broker orders, automatic M4 state
promotion, automatic planning-float conversion, ungated refresh, production
changes and separate Experimental product/runtime development.

The previously listed authenticated canonical M7 observation-to-route adapter is
not a valid current software gap. Legacy M7 requires canonical M6
`PaperSimulationInput`/`PaperSimulationResult` and independently reruns
`simulate_paper()`. Shadow audited lifecycle facts do not supply that canonical
M6 input, and M7.1 explicitly preserves replay observations without converting
them into legacy M6 evidence. `/performance` must therefore remain fail-closed
until a genuine canonical M6 or M6.1 observation source exists.

Remaining REAL_SOFTWARE_GAP/PARTIALLY_DONE work: evidence-bound
security-type/stock/ETF comparison and broker-specific dated schedule contracts,
subject to the existing evidence boundaries and without inventing classifications,
quotes, FX, fees or broker rates.

The missed-collection existing-UI transport is now CLOSED in this milestone.
Authenticated MISSED / NOT SCORED receipts remain distinct from zero-candidate
watchlists, and damaged receipts fail closed without candidate reconstruction.

**TODAY_TARGET_STATUS: CONTINUE.** This milestone closes the remaining bounded
Shadow lifecycle visibility gaps identified during this review, but the broad
software target is not yet declared complete. M7 operational wiring is now
classified EVIDENCE_BLOCKED rather than a current software implementation gap.
The next software review should scope the evidence-bound security-type/stock/ETF
and broker-specific dated-schedule boundaries. Do not add arbitrary report
ingestion, convert Shadow facts into M6 evidence, or derive M7 trades from NAV.

Live readiness remains unestablished. Tests do not supply authentic data,
profitability, empirical validation, operational validation or authorization for
real-money execution.
