# Whole-platform software review — 2026-09-25

Repository: `/home/egx-agent/work/egx-trading-platform-us`.
Branch: `agent/er1c-free-acquisition`.
Mission baseline: `1247371fd7497c54111bed95e0971574e305937d`.
Review starting HEAD: `964247cc0310be93c03ba2c516a400337510825f`, clean worktree.

This is the current gap matrix. It supersedes historical next-step and incomplete
classifications in TODAY_SOFTWARE_RECOVERY, TODAY_SHADOW_LIFECYCLE_DELIVERY,
SOFTWARE_SPRINT_AUDIT and STATE.md. Those documents retain milestone history.
The latest 120 commits, execution plan, current delivery documents, implementations,
consumers and tests were reconciled before this documentation change. No new
implementation gap was demonstrated in this final bounded review. This is a
software completion assessment, not proof of authentic evidence or live readiness.

## Final platform gap matrix

Each row is one separately classified candidate. Software contracts and their
missing authentic inputs are deliberately separate rows.

| Candidate | Classification | Current implementation / consumer / validation evidence |
| --- | --- | --- |
| TODAY/session truth | ALREADY_DONE | `app/ui/today.py` reads a consistent read-only snapshot, checks exact-date canonical session/column agreement and leaves missing truth UNVERIFIED; `test_today_ui.py` |
| Calendar maintenance, verification and scheduler wiring | ALREADY_DONE | `app/core/calendar_live_runtime.py` wires retained canonical index evidence to verification; calendar maintenance and scheduler integration tests cover gates; `test_calendar_live_runtime.py` |
| Official-index and daily refresh admission / quota | ALREADY_DONE | `app/data/official_index_refresh_runtime.py`, `daily_refresh_admission.py`, `quota.py`; runtime, admission, quota and end-to-end tests; no refresh enablement follows from software completion |
| Immutable data, canonical validation, PIT and stale rejection | ALREADY_DONE | `app/data/point_in_time.py`, raw/canonical stores and `app/research/historical_pit.py`; `test_m3_point_in_time.py`, raw-store and R1 tests |
| Research engines and explicit candidate admission | ALREADY_DONE | `app/strategies/eod.py`, `intraday.py`, `app/paper/shadow_candidate_admission.py`; M4 and admission tests; no automatic conversion of planning floats into execution geometry |
| Collection producer and operator CLI | ALREADY_DONE | `shadow_producer.py` and `tools/produce_shadow_watchlist.py` publish audited completion/ledger before the UI input; `test_shadow_producer.py` exercises CLI, publication failure, late cutoff and existing UI consumption |
| Explicit MISSED / NOT SCORED collection | ALREADY_DONE | `read_shadow_missed()` and existing collection/report audit feed the existing UI; never reconstruct a watchlist from a missed session |
| Trigger/fill/open-position/reservation/exit/continuation/settlement | ALREADY_DONE | Existing `app/paper/shadow_*` writers/auditors, `shadow_report.py` consumers and `read_shadow_execution()` schemas; corresponding core and `test_main_shadow_*` integration tests |
| Fill-policy and exit fact-receipt chronology | ALREADY_DONE | `fd0ddb0` enforces completion/selection/publication ordering; `964247c` binds exit publication to evaluation-fact admission; exact-boundary, rejection, tamper and read-only tests |
| Capital, sizing, exposure and loss limits | ALREADY_DONE | `app/risk/engine.py`, `shadow_portfolio.py`, `shadow_allocations.py`; M5 and Shadow allocation tests; unsettled CLOSED exits cannot silently release capital |
| Native cash, portfolio, snapshots and NAV/valuation intervals | ALREADY_DONE | `shadow_daily_portfolio.py`, `shadow_daily_snapshots.py`, `shadow_daily_series.py` and independent existing UI adapters; portfolio/snapshot/series and UI tests require complete reservation ancestry and marks |
| Exact-dated US security/instrument classification | ALREADY_DONE | `1247371`, `shadow_security_type.py`, `read_shadow_security_types()`; identity/date/MIC/evidence/candidate binding and UI tamper tests |
| Stock/ETF comparison and allocation recommendations | EVIDENCE_BLOCKED | Security type does not establish comparable ETF observations, suitability or allocation evidence; current UI explicitly reports comparison unavailable |
| Generic paper economics | ALREADY_DONE | M6 `PaperExecutionConfig`, Shadow fill/exit policies bind costs, currency, slippage, participation and evidence packages; simulator, fill, exit and UI tests |
| Verified IBKR applicability and date-effective rates | EVIDENCE_BLOCKED | No verified broker/account/tier/applicability evidence; UI reports NOT ESTABLISHED; generic economics must not be represented as an IBKR quote |
| Speculative broker-specific schedule schema | NOT_NEEDED | No current caller requires a broker schedule object, and the execution plan requires generic transaction economics rather than a broker tariff engine. Adding an unused schema without established applicability requirements closes no demonstrated software defect; see rationale below |
| Legacy M7 analysis, replay M7.1 and renderer | ALREADY_DONE | `app/performance/models.py` reruns canonical M6 simulation; `analyzer.py`, `replay.py`, `app/ui/performance.py`; M7, M7.1 and UI tests preserve separate contracts |
| Operational M7 source and report population | EVIDENCE_BLOCKED | `/performance` has no authentic canonical M6/M6.1 observation source. Shadow lifecycle and NAV cannot supply or substitute that input |
| Observed intraday/daily/multi-session intervals | ALREADY_DONE | Intraday engines, replay, continuation and actual dated valuation-series contracts; gaps remain gaps; continuation and series tests |
| Predictive holding horizons/probabilities | EVIDENCE_BLOCKED | Elapsed observed intervals do not validate predictions; no supported estimation input or validation sample is available |
| M8/US8 research validation software | ALREADY_DONE | `app/research/splits.py`, `evaluation.py`, `app/us/research_validation.py`; M8/M8B/US8 tests cover purge, cost sensitivity, OOS, confidence and frozen holdout boundaries |
| Authentic historical and forward evidence / empirical validation | EVIDENCE_BLOCKED | Exact historical identity/universe, every-date sessions, complete bounded actions, edition availability, independent review, actual bars/costs and timely forward inputs remain missing; R1/ER1/ER1B/ER1C admission and real OOS/holdout/forward samples remain unestablished |
| Existing API/UI transport and audit consumption | ALREADY_DONE | `app/main.py`, `app/ui/shadow.py`, `shadow_input.py`; fixed bounded canonical envelopes re-audit upstream receipts; missing stages remain unavailable; existing API/UI tests cover tampering and read-only behavior |
| Additional automatic execution producer, stage inference or report uploads | NOT_NEEDED | Explicit public lifecycle operations and read-only stage consumers exist. The authorized product does not require automatically inferring an execution stage or accepting caller-computed performance |
| Separate Experimental product/runtime | OBSOLETE_OR_SUPERSEDED | Historical compatibility infrastructure; existing `app.main` is the product; no launch or promotion work is required |
| Closed free-source/rights acquisition paths | OBSOLETE_OR_SUPERSEDED | Prior NO_GO qualifications stand; re-probing is forbidden and is not a software backlog |
| Live execution, broker orders, automatic promotion/geometry conversion, ungated refresh, production changes | DELIBERATELY_DISABLED | Current safety boundaries remain in force; software completion supplies no execution or deployment authorization |
| Older recovery backlog statements | OBSOLETE_OR_SUPERSEDED | Current notices link this matrix; completed collection/lifecycle/identity integrations and evidence-blocked M7 are not reopened |

## Broker contract disposition

Inspected `PaperExecutionConfig`, `ShadowFillPolicy`, `ShadowExitPolicy`, their
consumers, evidence-package validation and UI economics output. The existing
contracts implement explicit generic assumptions; they do not model every broker
tariff and do not claim to. No broker/account/tier/date-effective schedule object
is consumed by the current platform. No verified applicability requirements or
authentic schedule were supplied. The execution plan's transaction-cost,
slippage and fill requirements are already implemented and tested.

Consequently, verified IBKR economics is EVIDENCE_BLOCKED; a speculative unused
schedule contract is NOT_NEEDED for this bounded mission. This does not claim
broker-specific software exists or that generic bps/fixed costs can encode every
future tariff. A future explicit broker-paper requirement with known scope could
justify a new contract and consumer. No broker pricing, fees, FX, tiers or
applicability were inferred or fetched during this review.

## Remaining evidence and limitations

The exact evidence blockers are: R1/ER1/ER1B/ER1C source admission; historical
identity/universe, every-date sessions and complete bounded actions; historical
edition availability and approved independent review; authentic executable bars
and dated economic inputs; timely admitted forward collections; canonical M7
observations; comparable ETF evidence; IBKR applicability and FX; validated
predictive horizons; real OOS, untouched holdout and forward validation samples.
Artificial tests establish software behavior only. No evidence package, candidate,
market observation, fill or validation result was manufactured for operations.

Existing operator-owned filesystem/local-clock trust limits still apply. Hashes
establish integrity rather than independent attestation. This review does not
claim resistance to arbitrary concurrent malicious filesystem mutation, active
deployment validation, fresh market coverage, profitability or LIVE readiness.

## Milestones and final handoff

The resumed mission baseline already contained `1247371` (audited security types).
The supervisor subsequently committed `fd0ddb0` (fill-policy chronology) and
`964247c` (exit fact-admission chronology). This cycle completes the whole-platform
classification and reconciles the current documentation; no Python behavior or
test expectations were changed. The complete regression result is recorded below.

No authorized REAL_SOFTWARE_GAP remains identified after this review. The final
administrative gate remains the documentation commit followed by a clean-worktree
check at the supervisor's committed HEAD. Do not invent another implementation
milestone merely to continue. Do not report the final clean state before the bridge
has actually processed this request.

TODAY_TARGET_STATUS: CONTINUE (final commit/clean-worktree gate pending only).
LIVE_READY: NO.

Next cycle: verify the supervisor commit contains exactly this documentation
milestone, verify clean branch/HEAD and unchanged tested implementation, and record
TODAY_TARGET_STATUS: REACHED with AUTOPILOT_STATUS: DONE if those checks pass.

## Validation and safety record

Full offline repository command:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python -m pytest -q
```

Result: **2744 passed in 204.17s (0:03:24)**. The repository autouse socket-denial
fixture applied. No tests were changed, hidden or weakened. In-memory `compile()`
checks passed for `app/paper/shadow_fills.py`, `app/paper/shadow_exits.py` and their
two test modules (the Python changed since the mission baseline), without writing
bytecode. No Python was changed by this documentation milestone.

`git diff --check` passed. The complete tracked diff and new matrix were reviewed;
the new file's whitespace/final newline, all three incoming documentation links
and all 28 single-classification rows were checked. Git index remained empty.
No external API/network calls, acquisition, rights probes, production access,
runtime/service/database changes, broker actions or secrets access occurred.
No direct staging or commit commands were run.

Cycle starting and ending HEAD: `964247cc0310be93c03ba2c516a400337510825f`.
Four documentation paths await the supervisor bridge: this file,
`docs/TODAY_SOFTWARE_RECOVERY.md`, `docs/TODAY_SHADOW_LIFECYCLE_DELIVERY.md` and
`docs/SOFTWARE_SPRINT_AUDIT.md`. Commit request message:
`docs: reconcile final platform software gap matrix`. The tree is intentionally
dirty pending that isolated commit; no future commit ID or clean state is claimed.
