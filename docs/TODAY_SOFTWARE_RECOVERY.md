# Existing-platform software recovery — 2026-09-19

> Current status (2026-09-25): see [the final platform gap matrix](PLATFORM_SOFTWARE_COMPLETION.md).
> This file is a historical milestone report. Its future-work statements below
> are superseded: collection/lifecycle/security-type integrations are implemented;
> M7 operational source, ETF comparisons and verified IBKR applicability are
> evidence-blocked. A speculative unused broker schema is not a current software gap.

Starting branch: `agent/er1c-free-acquisition`.
Starting HEAD: `97b918cb1355dd0f6237a92edcf3bdac7e465c17`.
Initial worktree: clean. The mission's explicit repository/branch and commit-bridge
instructions supersede the older AGENTS workspace, development branch and direct
commit instructions. Production access and live execution remain prohibited.

## Reconstruction completed before implementation

Read AGENTS.md and EXECUTION_PLAN.md in full, inspected the latest 110 commits,
path-specific history, milestone/status documentation and implementation/tests for
M3/M4/M5/M6/M7/M8, retrospective PIT, forward Shadow, the existing UI and calendar
scheduler path. Baseline full regression: **2646 passed in 162.64s**.

Newer implementation and tests supersede historical stop points in AGENT_STATUS,
M3/M5/M7 documents and early sections of FORWARD_SHADOW_PROTOCOL. The append-only
protocol document describes successive milestones; its earlier "next" paragraphs
are not a current backlog. No historical source or license probe was repeated.

| Area | Classification at reconstruction | Evidence and disposition |
| --- | --- | --- |
| M0/M1/M2 | ALREADY_DONE | Offline test guard, calendar/scheduler contracts and quota admission; no enablement required |
| M3 | ALREADY_DONE engineering / EVIDENCE_BLOCKED actual PIT coverage | Immutable storage, canonical validation, dated reviewed universe/actions and stale rejection already implemented |
| M4 | ALREADY_DONE research engines | Swing, Pre-Surge, First15, ORB, VWAP and momentum contracts; UNVALIDATED, execution_allowed=False |
| M4 to Shadow | PARTIALLY_DONE / REAL_SOFTWARE_GAP | 97b918c introduced explicit admission but no operational caller connected it to collection, candidate ledger and UI input |
| M5/M6 | ALREADY_DONE | Risk arithmetic, simulated execution, replay and Shadow capital/settlement/continuation contracts and tests |
| M7 | ALREADY_DONE analytics; EVIDENCE_BLOCKED operational report | Analyzer, replay analytics, renderer and authenticated periodic-return bridge exist; no authenticated M7 trade-observation source is supplied to the route |
| M8 / US8 | ALREADY_DONE validation engine / EVIDENCE_BLOCKED empirical validation | Purged rolling partitions, OOS, holdout, confidence and cost-sensitive analysis; fixtures do not establish edge |
| R1 / ER1 / ER1B / ER1C | ALREADY_DONE contracts and qualification / EVIDENCE_BLOCKED admission | Closed free-source roster/session/action paths remain NO_GO; source availability and independent reviews also absent |
| Existing candidate/execution UI | ALREADY_DONE | 68634a3, bb568d5, 9423469 integrate audited candidate, same-session and continuation readers |
| Capital/portfolio/series UI | ALREADY_DONE, with one REAL_SOFTWARE_GAP | 4b1c9e8, 9153ec4, 4b384a2 integrate readers; missing candidate transport unnecessarily suppressed independently auditable snapshots/series |
| Calendar Live / official index / TODAY session | ALREADY_DONE | 92f4064, 23c8297, 0ef881e; local verification consumes retained canonical evidence and fails closed |
| Stock / ETF / cash | PARTIALLY_DONE / EVIDENCE_BLOCKED comparison | Native cash/reservation/position accounting exists. US identity contracts recognize ETF, but Shadow snapshots do not establish asset-class classification or comparative ETF evidence |
| IBKR-aware costs | PARTIALLY_DONE / EVIDENCE_BLOCKED broker applicability | Evidenced entry/exit costs, slippage and participation exist; no verified IBKR schedule binding exists |
| Multiple horizons | PARTIALLY_DONE / EVIDENCE_BLOCKED validated horizons | Intraday, daily, continuation and actual valuation intervals exist; no evidence validates predictive horizons or probabilities |
| Separate Experimental UI/runtime | OBSOLETE_OR_SUPERSEDED product surface | Retained historical/test infrastructure only; no extension, startup or promotion performed |
| Live orders / automatic promotion / ungated refresh | DELIBERATELY_DISABLED | No broker execution, no state-to-BUY mapping, no planning-float conversion, no refresh enablement |

## One coherent delivery milestone: explicit collection to existing UI

The producer closes the confirmed chain:

`M4 Candidate + explicit ShadowCandidateAdmission -> ShadowWatchlist ->
complete_watchlist() -> append_candidate_event() -> input.json -> /shadow`.

`produce_strategy_watchlist()` accepts a complete `StrategyShadowRequest`.
Selections contain exact M4 candidates and explicit admissions. Candidate and
admission fields are revalidated against unchecked model copies. Each strategy
decision must precede or equal the watchlist information cutoff. The generation
clock is actual local UTC, not a supplied receipt clock. Session, package review,
availability, identity bindings and completion-before-cutoff gates remain intact.

The producer requires a new absolute unlinked collection directory under an
already-existing trusted parent. It preserves `strategy-source.json`, completes
the frozen watchlist, appends and audits its candidate event, then publishes the
existing bounded `input.json` envelope last. It never overwrites another
collection. Interrupted work may leave unscored artifacts without UI input;
there is no automatic rollback, retry, historical reconstruction or promotion.
An explicit empty selection is permitted only with coherent explicit session and
evidence inputs; it is NOT SCORED, never an inferred successful no-trade result.

This is an operator-invoked producer for supplied research outputs, not an
automatic strategy scheduler or evidence authenticator. The original engine
inputs and source review must be retained by the operator. Digests and local
clocks establish integrity, not independent attestation or strategy validity.
A fresh collection directory is a bounded collection boundary, not a new
portfolio or a license to allocate independent full capital to each session.
Shared portfolio policy/reservations remain the existing separate audited gates.

The existing UI now:

- shows dated frozen candidates on TODAY separately from the daily-data inventory;
- describes data validation without claiming fresh, eligible universe membership;
- keeps independently audited portfolio snapshots and series visible if candidate
  transport is missing or malformed, while execution still requires its candidate
  ancestry; unsafe linked directory state blocks every stage;
- exposes the already-audited entry/exit economic policies and their evidence IDs,
  with IBKR applicability explicitly NOT ESTABLISHED and no broker quote claim;
- presents native cash, reserved capital, observed gross market value and dated
  gross NAV clearly, without inventing stock/ETF classifications or buying power;
- presents actual dated valuation intervals without gap filling, annualization or
  validated holding-horizon claims;
- retains unavailable M7 performance and links to the real paper observations.

The `/api/shadow` `available` flag continues to mean **candidate collection
available**. Consumers must inspect `portfolio` and `series` independently;
those values may be present when the collection flag is false. Each still runs
its complete existing audit. A UI read creates no receipt, fill, position or NAV.

## Offline operator interface

Use the same installed interpreter as the application. From the repository root:

```text
python -m tools.produce_shadow_watchlist REQUEST_JSON NEW_ABSOLUTE_COLLECTION_DIRECTORY
```

`REQUEST_JSON` is the complete canonical JSON representation of
`app.paper.shadow_producer.StrategyShadowRequest`. Exact top-level fields:
`record_id`, `information_cutoff`, `session`, `evidence`, `selections`,
`evidence_packages`. Each selection has exactly `candidate` and `admission`,
containing every field of their canonical models, including explicit nulls and
defaults. Arrays represent tuples; Decimal values are strings; clocks require
explicit UTC; M4 planning float fields require finite JSON floating numbers.
Duplicate/extra/missing fields and linked inputs are rejected. No authentic
example request is invented or supplied by this milestone.

The existing app's `EGX_SHADOW_DIRECTORY` selects the completed directory for
read-only display. This milestone does not set that variable in a running
service, launch a runtime, deploy, refresh, or restart anything. Do not point
experimental work at production state. Genuine forward collection needs genuine
reviewed inputs and completion before its future decision cutoff; missed dates
must not be reconstructed. Existing shared-capital and execution APIs remain
separate explicit operations, never consequences of running this producer.

## Source of truth for each user surface

| Surface | Source of truth | Unavailable boundary |
| --- | --- | --- |
| TODAY data | Read-only consistent SQLite snapshot of validated daily artifacts | Structural quality is not freshness, universe eligibility or a signal |
| TODAY session | Exact-date persisted MarketSession, canonical payload/column agreement | Missing/unverified session stays UNVERIFIED; no weekday or post-close shortcut |
| TODAY / Shadow candidates | Explicit input plus audited frozen completion and candidate ledger | No input, late freeze or corrupt ancestry means no candidate display |
| Shadow execution | Authenticated facts, frozen policies, trigger/fill/position/exit receipts, optional continuation chain | Candidate is not fill; missing position event is not position; UNKNOWN remains UNKNOWN |
| Capital/risk | Audited policy, reservation and settlement lineage | A closed exit is not itself settlement or portfolio capital |
| Portfolio/NAV | Dated native snapshot, complete frozen ledger and one authenticated mark per active reservation | Historical gross valuation, not current NAV, liquidation value or FX-combined wealth |
| Valuation intervals | Independently audited dated snapshots with actual reporting gaps | Valuation changes are not M7 observations or validated holding horizons |
| PERFORMANCE | Existing M7 canonical analysis contract | No authentic observation source supplied; unavailable, no NAV-derived observations |
| Stock/ETF/cash | Authenticated cash and individual positions only | Asset-type comparison and allocation recommendations not established |
| Paper economics | Exact audited entry/exit policy and evidence-package IDs | Verified IBKR applicability unavailable; no inferred broker rate |

## Remaining evidence and intentionally disabled work

EVIDENCE_BLOCKED: exact historical universe/identity, every-date sessions, complete
bounded actions, historical edition availability, independent review, authentic
execution bars and dated economic inputs for historical validation. Forward
collection also needs actual timely admitted inputs; no authentic collection was
created during this mission. M7 needs actual canonical observations, and M8 needs
real OOS/untouched holdout evidence. ETF comparisons, IBKR applicability, FX and
validated holding horizons cannot be inferred from existing receipts.

DELIBERATELY_DISABLED: live-money execution, broker orders, automatic research
state promotion, automatic geometry conversion, ungated refresh, production
changes and separate Experimental product/runtime development.

The scoped producer does not schedule M4 automatically or replace manual explicit
admission. PERFORMANCE does not yet have a disk adapter for a future authentic
M7 observation source. Stock/ETF comparison and broker-specific schedule
contracts are not implemented by adding explanatory UI text. These remain
future software integration work as well as evidence dependencies; they are not
claimed complete merely because unavailable states render correctly.

## Verification and commit handoff

All tests use the repository network-denial fixture and isolated synthetic state.
No fixtures were published as authentic inputs. No external market-data/API or
terms/license requests, production actions, broker orders, secrets access, git
staging or git commits were performed. The shared interpreter was read/executed;
no dependency installation or environment modification occurred. `/usr/bin/python3`
lacked pytest; verification used the existing shared project virtual environment.

Exact test commands and final results are recorded below after completion.

Files in this single collection/UI delivery milestone:

- `app/paper/shadow_candidate_admission.py` — revalidate copied candidates/admissions.
- `app/paper/shadow_producer.py` — explicit forward producer and canonical request.
- `tools/produce_shadow_watchlist.py` — bounded offline operator entry point.
- `app/main.py` — pass audited candidate state to the existing TODAY presenter.
- `app/ui/shadow_input.py` — strict M4 float transport and audited economics display.
- `app/ui/shadow.py` — independent portfolio reads, economics and clearer valuation presentation.
- `app/ui/today.py` — dated candidate preview and accurate data/setup wording.
- `app/ui/performance.py` — unavailable-observation explanation and Shadow navigation.
- `tests/test_shadow_producer.py` — full producer/CLI/UI path and adversarial inputs.
- `tests/test_main_shadow_execution_ui.py` — costs remain bound to audited policies.
- `tests/test_main_shadow_portfolio_ui.py` — snapshot independence with full ancestry.
- `tests/test_main_shadow_series_ui.py` — independent series and unsafe-directory rejection.
- `tests/test_today_ui.py` — assertions updated to the corrected product semantics.
- `docs/TODAY_SOFTWARE_RECOVERY.md` — reconstruction, runbook and handoff results.

Exact focused commands (repository working directory; all offline):

```sh
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python -m pytest -q tests/test_shadow_producer.py tests/test_shadow_candidate_admission.py tests/test_main_shadow_ui.py
```

Initial producer result: **39 passed in 1.25s**.

```sh
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python -m pytest -q tests/test_shadow_producer.py tests/test_shadow_candidate_admission.py tests/test_main_shadow_ui.py tests/test_main_shadow_execution_ui.py tests/test_main_shadow_settlement_ui.py tests/test_main_shadow_portfolio_ui.py tests/test_main_shadow_series_ui.py tests/test_today_ui.py tests/test_m7_performance_ui.py tests/test_experimental_ui.py
```

First integrated run: **169 passed in 24.13s**. Final integrated run, including
bounded publication and audited economics regressions: **171 passed in 30.28s**.
The Experimental UI tests are compatibility regression only; no Experimental
implementation file was modified.

Full baseline and final regression command:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python -m pytest -q
```

No failed tests were hidden or weakened. Existing TODAY wording assertions changed
with the corrected wording; their no-recommendation/session-safety assertions remain.

Final full regression: **2679 passed in 172.03s (0:02:52)**.
`git diff --check`: clean. Complete tracked diff and all new-file contents reviewed;
new files also checked for trailing whitespace and final newline. No index changes.

Ending HEAD before supervisor handoff remains
`97b918cb1355dd0f6237a92edcf3bdac7e465c17`; supervisor commits observed during this
mission: **none**. The complete 14-path changed/untracked set is submitted through
`/home/egx-agent/er1-autopilot/state/COMMIT_REQUEST.json` for one conventional commit.
Codex did not stage or commit. Supervisor completion requires this invocation to
exit; the request is not a claim that a commit already exists.

**TODAY_TARGET_STATUS: CONTINUE.** The verified producer gap and independent UI
read-path gap are closed, and the existing product now exposes more of its actual
audited state. The broad whole-project completion gate is not claimed: a future
authenticated M7 observation source still needs route integration, and stock/ETF
comparison and broker-specific schedule contracts remain software work coupled
to evidence requirements. No repeated acquisition or license probing is needed
to continue that software review. Commit this coherent milestone, then begin the
next supervisor cycle by re-reading HEAD/status and prioritize that remaining
canonical evidence-to-user-surface integration. Do not synthesize missing inputs.

Live readiness remains unestablished: authentic evidence, empirical validation,
forward sample accumulation and operational validation are absent, and live-money
execution is prohibited. Test success does not alter those conclusions.
