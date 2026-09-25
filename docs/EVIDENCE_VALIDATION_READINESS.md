# Phase 2 — evidence and validation readiness

Review date: 2026-09-25. Repository: `/home/egx-agent/work/egx-trading-platform-us`.
Branch: `agent/er1c-free-acquisition`. Starting and pre-commit HEAD:
`3285a271db51289ec11c036194b4da2641282cc8`. Initial worktree was clean; user was
`egx-agent`. The latest 120 commits were inspected before changes.

This is the authoritative Phase-2 evidence matrix. The current mission overrides
AGENTS.md's stale workspace/branch/direct-commit instructions and old plan stop
points. The software phase is closed: **TODAY_TARGET_STATUS: REACHED**.
The pending software handoff language in PLATFORM_SOFTWARE_COMPLETION and the
recovery/delivery documents was fulfilled by starting HEAD. Older acquisition
next steps and STATE.md backlogs are historical, not authorization to fetch data
or reopen software. No reproducible software defect was revealed in this review.

## Scope and reproducible evidence record

All retained research evidence is under `/home/egx-agent/research-data/er1c-us-pilot`.
The complete recursive inventory contains **67 regular files, 129,416,738 bytes**.
No linked evidence was encountered. Every byte size and SHA256 was recomputed;
the inventory exactly matched the earlier Phase-2 inventory in autopilot state.
Original evidence was not modified. Repository `data/` and `logs/` contain only
`.gitkeep`; tracked sources, documentation, test fixtures and ignored caches were
checked as distinct from empirical inputs. No authentic package or observation
source was found there. Autopilot experimental metadata/logs describe historical
software builds/runtimes, not an authentic market-evidence collection. Production
and secrets were not inspected.

Derived audit artifacts, containing metadata and results rather than original
market files:

- [Complete retained inventory](evidence-validation/retained-inventory.json): exact paths, sizes and hashes, relative to research-data.
- [Retained manifest metadata](evidence-validation/retained-manifests.json): exact source locators, declared editions where present, original UTC receipt fields and source hashes. Missing fields remain missing; filesystem dates were not substituted.
- [Fresh local audit outputs](evidence-validation/local-audits.json): all 12 existing auditor calls, exact arguments, source HEAD, audit clock and results. Socket connect, connect_ex, create_connection and DNS resolution were explicitly denied during this run.

Manifest metadata is a retained assertion. Hash agreement establishes local
integrity, not source authenticity, independent attestation, historical edition
availability, licensing, or human approval. Current capture clocks are not 2022
publication clocks. No new HistoricalEvidencePackage was manufactured to bypass
missing mandatory information.

## Artifact catalogue

Paths below are relative to the retained pilot root. Every filename and hash,
including sidecars, is in the inventory; complete receipt fields are in the
manifest metadata. These group IDs also identify the exact artifacts inspected
for each matrix row.

| ID | Exact retained directory/artifacts | Coverage and provenance | Timing / review |
| --- | --- | --- | --- |
| A | `20260912T152213Z/ibm_2022-04-01_2022-11-04.json.raw`, `twtr_2022-04-01_2022-11-04.json.raw`, `manifest.json`, `manifest.sha256` | Tiingo raw daily OHLCV and vendor dividend/split markers; frozen predeclaration/hash verified | Receipts 2026-09-12T15:22:13.986153+00:00 and 15:22:14.415033+00:00; exact historical consumed-edition availability and approved review absent |
| B | A's `evidence/`: `sec_twitter_merger_8k.html`, `sec_nyse_twitter_removal_notice.html`, `sec_ibm_submissions.json`, `sec_ibm_2022q2_10q.html`, two `ibm_2022-04-26_dividend_notice.html` / `ibm_2022-07-25_dividend_notice.html` notices, `ibm_cash_dividend_history.html`, `ibm_stock_split_history.html`, `nyse_2022_trading_calendar.pdf`, `evidence_manifest.json` and sidecar | SEC/IBM/NYSE sources; filing/event/calendar corroboration only | Individual receipts 2026-09-12 15:32:46.869388 through 16:45:29.126531 UTC; historical availability explicitly unproven; no approved package review |
| C | `nyse-roster-probe-20260912/`: mapping index HTML, three `nyse_short_volume_20220701.txt`, `nyse_short_volume_20221027.txt`, `nyse_short_volume_20221028.txt`, manifest/sidecar | NYSE daily activity reports: 7,432 / 7,448 / 7,413 rows; IBM present on all three; TWTR absent on October 28. Activity is not a roster | 2026 capture; no historical availability or approved complete-roster review |
| D | `sec-listing-ledger-probe-20260912/`: Q2/Q3/Q4 `sec_2022_*_form.idx`, `sec_exchange_delistings.html`, `sec_accessing_edgar_data.html`, manifest/sidecar | SEC filing indexes; 25-NSE counts 344/416/780; 8-A12B 225/226/205; amendments 13/34/26. Filing discovery, not venue/class-complete listing ledger | Exact retained receipt fields preserved; 2026 capture does not prove decision-time availability; no canonical review |
| E | `sec-twitter-submission-probe-20260912T191134Z/`: `twitter_20220418_8a12b_submission.txt`, `twitter_20221028_25nse_submission.txt`, manifest/sidecar | First registers preferred-stock purchase rights, not common-stock listing; second corroborates TWTR suspension/removal | 2026 receipts, exact SEC submission identity/envelopes checked; no complete-coverage or historical-availability review |
| F | `sec-form-sample-20260912T194500Z/`: declaration/sidecar, `acquisition_attempts.json`, `direct_submission_attempt_20260913T010403Z.json` | Six return-independent sample selections, two forms across three quarters; failed acquisition reports, no corresponding acquired sample submissions | Initial result-recording time is explicitly not a request clock; direct attempt bound by existing auditor; HTTP 403 supplies no market fact |
| G | `forward-session-truth-20260913T005146Z/`: `nyse_2026_calendar.pdf`, `nyse_hours_calendars.html`, manifest/sidecar | General NYSE schedule, intended first US date 2026-09-14; exact target-date session remains UNKNOWN | Receipts 2026-09-13T00:51:46Z; pinned manifest/editions pass; approved session package and timely frozen watchlist absent |
| H | `nyse-removal-scope-20260913T053247Z/`: `nyse_delistings.html`, manifest/sidecar | Current temporary pending-removal publication policy, not complete 2022 historical roster | Receipt 2026-09-13T05:32:47.235776Z; historical availability false; no approved review |
| I | `nyse-corporate-actions-scope-20260913T053527Z/` and `nyse-corporate-actions-spec-20260913T053952Z/`: policy/product HTML, `nyse_corporate_actions_client_spec_v2.2.6.pdf`, manifests/sidecars | Upcoming-event policy and product/schema documentation for over 60 types; no pilot event files or explicit complete empty coverage | 2026 receipts, historical availability unproven, no approved event-coverage review |
| J | `nasdaq-halt-qualification-20260913T061309Z/`: RSS documentation, queries, terms PDF, manifest/sidecar; `nasdaq-halt-fields-20260913T061742Z/`: field definitions, trading-halts HTML, manifest/sidecar | Documentation only; zero feed observations. Non-NASDAQ is not XNYS; scheduled resumption is not actual execution | Latest receipts 06:13:11.443263 and 06:17:43.259300 UTC on September 13; retention/transformation permission unresolved; no issue-specific canonical review |
| K | `nyse-symbol-mapping-spec-20260913T062754Z/`: BQT client v2.2l and March-2022 filename-change PDFs, manifest/sidecar | Schema documentation; zero pilot-date mapping editions; common-stock field not established in reviewed layout; no stable listing continuity | Latest receipt 2026-09-13T06:27:56.470678Z; historical timing and retention/transformation rights unproven |
| L | Root `closed-inventory-audit.json`, `numeric-boundary-audit.json`, `ibm-dividend-scope-audit.json`, `ibm-table-role-audit.json` | Older derived reports, all NO_GO; not new source evidence | Superseded for current results by fresh auditors; original bytes retained unchanged |

The current public-evidence manifest is 4,702 bytes with SHA256
`65f29e370f3b20330b00719247518f8242ae59c010f3bb4ecfdbe7041c01bea6`.
An earlier table in ER1C_US_PILOT_FINDINGS describes its smaller historical
edition; it must not override the current retained bytes and closed-inventory audit.

## Canonical gate reconciliation

Inspected current R1 `HistoricalEvidencePackage` / `require_historical_evidence`,
US1/US2/US3/US4/US5A/US5B and US7B admission boundaries, Shadow collection and
MISSED contracts, canonical M7 input/result replay, M8 and US8 selection/evaluation
contracts and corresponding tests. Relevant delivery/recovery, ER1/ER1B/ER1C,
R1, forward and M8/US8 documents were reconciled with current implementations.

R1 requires a known exact edition, bound receipt/hash, field/scope-specific
availability proof with exact matching attachments, and approved review.
Availability must be no later than the decision; receipt, attachments and review
must be no later than research build. Human/source truth is not produced by a
model constructor. US composition additionally requires exact-date stable
identity and universe, every-calendar-date session evidence and complete bounded
actions. A narrow corroborated fact cannot substitute for any of those packages.

In the matrix, **NO_GO** means canonical admission is unavailable; it does not
mean the source-byte audit failed. **NOT RUN** means no genuine admitted input
exists for empirical evaluation. All rows inherit the exact provenance/timing/
review states of their catalogue groups. “None” means inventory and repository
inspection found no such authentic artifact, with provenance/timing/review all
absent. No classification is a claim of edge or empirical readiness.

| Domain | Classification | Inspected artifacts; coverage | Admission / validation performed and result | Exact remaining requirement; external or authorization |
| --- | --- | --- | --- | --- |
| Retained byte integrity and scoped source facts | VALIDATED_FROM_LOCAL_EVIDENCE | A–L; 67 files; exact coverage above | 12 existing auditors PASS, H manual integrity/scope PASS; original hashes unchanged | No remaining local integrity work; integrity alone admits no observation |
| R1/ER1/ER1B/ER1C authentic composite admission | LOCAL_EVIDENCE_INCOMPLETE | A–L; IBM/TWTR 2022-04-01–11-04 target | Pilot auditor NO_GO; R1/US contract regression run | Complete identity/universe/session/action/timing/review packages; external evidence and approved review required |
| US raw daily observations | LOCAL_EVIDENCE_INCOMPLETE | A: IBM 151 rows 04-01–11-04; TWTR 146 rows 04-01–10-28 | Numeric/hash/date audit PASS; zero canonical admitted bars; TWTR 10-28 quarantined, not executable | Source edition availability/review and upstream exact-date gates; external evidence and review |
| Exact historical identity | LOCAL_EVIDENCE_INCOMPLETE | B/E: IBM Q2 filing, TWTR exact submissions; C/K discovery/specs | US1 NO_GO; no continuity inferred from issuer CIK or ticker | Stable instrument/listing crosswalk, MIC/security-type/provider bindings for each required date; external evidence |
| Historical universe / survivorship coverage | LOCAL_EVIDENCE_INCOMPLETE | C/D/E/F/H/K, three activity dates and filing discovery | US5A NO_GO; no activity/index/current-list projection | Complete exact-date then-members including removed names and independent completeness review; external evidence |
| Every-date historical sessions | LOCAL_EVIDENCE_INCOMPLETE | B 2022 annual PDF | US2 NO_GO; calendar scope audit PASS | Every calendar date/MIC with explicit open/early/closed state and exact UTC hours, correction/timing evidence; external evidence |
| Bounded corporate actions | LOCAL_EVIDENCE_INCOMPLETE | A/B/E/I; two IBM announcements/payments, split-history scope, one TWTR merger/removal | US4 NO_GO; event/notice/payment/split semantic audits PASS | Complete all-type bounded coverage or evidenced empty coverage, exact effective/ex-dates and terms; external evidence |
| Historical edition availability | EVIDENCE_BLOCKED_EXTERNAL | A–K metadata: later receipts, no pre-decision availability proof | Existing auditors confirm unproven timing; R1 fails closed by contract | Authoritative exact consumed-edition publication/correction evidence with exact or bounded latest availability before decision; external evidence |
| Independent approved source review | AUTHORIZATION_BLOCKED | A–L: no approved review/attachment binding | No review invented or self-approved | Authorized independent human review of exact receipt, hash, scope, availability evidence and attachments; review must follow actual missing-evidence resolution |
| Retention/transformation/use rights | AUTHORIZATION_BLOCKED | J/K and retained ER1B qualification; earlier UNKNOWN remains UNKNOWN | Existing documentation auditors rerun locally; no new legal/rights investigation | Explicit applicable retention/transformation/use grant or qualified review of authoritative terms; separate authorization/evidence, no implied grant from public access |
| Authentic execution bars / volume | EVIDENCE_BLOCKED_EXTERNAL | None; A contains daily bars only; J no feed observations | US7B/M6/replay software tests run; empirical replay NOT RUN | Authentic final opening-origin contiguous M1/M5 OHLCV, interval/sequence/availability/source bytes, session and identity binding; external evidence |
| Authentic generic cost inputs | EVIDENCE_BLOCKED_EXTERNAL | None; software policies/fixtures are assumptions | Cost/replay regression only; empirical economics NOT RUN | Dated applicable fees/levies, units, defensible slippage/spread and participation evidence; external evidence |
| Verified IBKR account/tier/rates/FX | EVIDENCE_BLOCKED_EXTERNAL | None | NOT ESTABLISHED; no broker-specific tariff schema invented | Verified account/jurisdiction/product/tier applicability and date-effective rates, fee/FX evidence; external evidence and separate account authority where needed |
| Timely forward collection | LOCAL_EVIDENCE_INCOMPLETE | G only; requested EGX 2026-09-13 / US 2026-09-14; no authentic watchlist | Auditor: UNKNOWN session / NOT_READY / no frozen watchlist; no retrospective reconstruction | Exact reviewed sessions and genuine future-cutoff candidate/evidence collection completed on time; external forward evidence |
| Durable MISSED receipts | LOCAL_EVIDENCE_INCOMPLETE | G; no authenticated target-date session package | `record_missed_session` requires exact approved session evidence; no receipt written | Authenticated session/cutoff package before a durable MISSED claim; report remains NOT SCORED without inventing session truth |
| Canonical M6/M6.1 observations for operational M7 | EVIDENCE_BLOCKED_EXTERNAL | None; no authentic execution source in repo/data/logs or retained inventory | M7/M7.1 replay regression run; empirical performance NOT RUN | Authentic PIT strategy/risk/admission, execution bars/costs and canonical inputs/results; Shadow/NAV cannot replace them |
| Comparable ETF evidence | EVIDENCE_BLOCKED_EXTERNAL | None; frozen pilot is two common stocks | No comparison or allocation report produced | Exact-dated stable ETF identities, genuinely comparable admitted horizons/prices/actions/costs and evaluation scope; external evidence |
| Predictive horizon / probability | EVIDENCE_BLOCKED_EXTERNAL | None | NOT RUN; elapsed bars or valuation intervals are not predictive validation | Frozen prediction/label definition, admitted outcome sample, calibrated OOS/holdout/forward evaluation; external evidence |
| Real OOS / M8 / US8 | EVIDENCE_BLOCKED_EXTERNAL | None; F is an acquisition sample declaration, not a trading research protocol | Software OOS/purge/bootstrap/scenario tests run; real OOS NOT RUN | Authentic canonical dataset, fixed strategy/version, predeclared folds/criteria/regimes/cost scenarios and executable matured labels; external evidence |
| Untouched holdout | EVIDENCE_BLOCKED_EXTERNAL | None | NOT RUN; no holdout freeze/non-inspection history claimed | Independent pre-outcome frozen protocol and untouched later sample with matured labels and complete scenario coverage; external evidence/review |
| Forward validation sample | EVIDENCE_BLOCKED_EXTERNAL | G is schedule documentation only | Zero admitted forward collections/trades; validation NOT RUN | Timely admitted future collections and complete observed lifecycle/cost outcomes under frozen protocol; external evidence/time |
| EGX authentic PIT / official index / execution | EVIDENCE_BLOCKED_EXTERNAL | None in retained inventory; US corroboration cannot substitute | Existing EGX/calendar/index/R1 tests only; no VERIFIED day or refresh enabled | Original EGX prices/index/universe/identity/sessions/actions, availability and reviews, executable bars/costs; external evidence |
| 2008 / 2020 stress coverage | EVIDENCE_BLOCKED_EXTERNAL | None; pilot rows are 2022 | NOT TESTABLE from retained scope | Authentic era/cohort histories and all PIT/execution gates; cannot proxy or back-project pilot cohort; external evidence |
| Completed software contracts | ALREADY_VALIDATED | Current implementation/tests and completion matrix | Approved full offline suite: 2744 passed; approved local-socket tests: 2 passed; no product defect demonstrated | No implementation milestone reopened |
| Live execution / production deployment | AUTHORIZATION_BLOCKED | No such authority in Phase 2 | No operation attempted; LIVE_READY remains NO | Separate explicit authority and all empirical/operational gates; outside local scope |
| Synthetic fixtures as authentic evidence | NOT_APPLICABLE | Repository tests and software build artifacts | Excluded from admission and sample counts | Never an evidence substitute |

No domain remains LOCAL_EVIDENCE_AVAILABLE_NOT_YET_ADMITTED: useful retained
material was consumed through the existing offline qualification machinery, but
none satisfies the mandatory canonical admission contract. Missing source truth
is not SOFTWARE_DEFECT_REVEALED. The incomplete rows above have exhausted their
local paths; each now requires the specifically listed external evidence or review.

## Authentic counts and boundaries

The retained sample is **297 vendor daily rows**, not 297 admitted observations.
IBM has 151 rows and TWTR 146; the final TWTR row is all OHLC 53.70 with zero
volume, conflicting with retained pre-open suspension evidence. It stays
quarantined. Even the remaining rows are not canonical executable/PIT evidence.
TWTR merger effective 2022-10-27, suspension 2022-10-28 and formal removal
2022-11-08 are separate corroborated facts, not inferred continuity.

Canonical admitted PIT datasets/bars, executable trade observations, timely
forward collections, scored trades, OOS observations and holdout observations:
**zero established**. No empirical date range, profit factor, expectancy,
confidence, predictive value or portfolio-performance statistic is established.
The six SEC sample selections and three short-volume report counts are source
qualification counts, not trading sample sizes.

## Validation and safety record

All twelve callable names/arguments/results are retained in local-audits.json.
For H, independently recomputed manifest digest
`d39c22b404a954767a26c593f200b805c585060ee0557f23307e7a6fc4b663fe`,
artifact size/hash, exact three-file inventory, receipt order/status and scope
text: exchange-initiated publication from Form 25 through effectiveness
(generally 10 days); issuer-initiated from one business day after notification
through effectiveness. This is temporary publication scope only.
An initial ad hoc assertion assumed a filename-suffixed SHA sidecar; the retained
sidecar is digest-only. Corrected inspection passed. This was a check assumption,
not a failed repository test, source corruption or software defect.

Test commands use the existing shared interpreter read-only:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python -m pytest -q tests/test_er1c*.py tests/test_r1*.py tests/test_us*.py tests/test_m7*.py tests/test_m8*.py tests/test_shadow_collection.py
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python -m pytest -q
```

Focused result: **1293 passed in 162.54s (0:02:42)**.
Historical restricted-sandbox result: **2 failed, 2742 passed in 252.85s
(0:04:12)**. This is environment context, not the final regression result.
The two affected tests were:

- `tests/test_experimental_runtime.py::test_failed_candidate_preserves_previous`
- `tests/test_experimental_runtime.py::test_success_records_identity_without_stopping_child`

Both previously failed at `tools/experimental_runtime.py:106`, creating
`socket.socket(socket.AF_INET, socket.SOCK_STREAM)`, with
`PermissionError: [Errno 1] Operation not permitted`. Their child-process and HTTP
checks are mocked, but the tests retain real local listener creation.
The authorized operator reran these unchanged tests outside the restricted Codex
sandbox in the approved validation environment. **Both passed: 2 passed in
0.15s.** The unchanged complete repository suite then passed: **2744 passed in
212.31s (0:03:32)**. This is the final Phase-2 full-suite result.

Approved logs were read and verified during the commit-request cycle:

- `/home/egx-agent/er1-autopilot/state/phase2-approved-local-socket-tests.log`
- `/home/egx-agent/er1-autopilot/state/phase2-approved-full-suite.log`

These quiet pytest logs record counts and durations, not node IDs or Git HEAD.
Their attribution to the two named tests, the same HEAD and preserved WIP, and
unchanged product code is the explicit authorized operator attestation in the
approved Phase-2 handoff. Current Git checks independently confirm HEAD remains
`3285a271db51289ec11c036194b4da2641282cc8`, the index is empty, no tracked code or
tests differ from HEAD, and only the four documentation artifacts are untracked.
No product code or test was changed to obtain the approved result. The resolved
environment restriction is not a software defect or remaining commit blocker.
The full suite was not rerun in the restricted sandbox.

Logs are retained at `/home/egx-agent/er1-autopilot/state/phase2-focused-tests-current.log`
and `/home/egx-agent/er1-autopilot/state/phase2-full-tests-current.log`.
The earlier state log reporting 1391 passed is historical and is not substituted
for either current command. `git diff --check` and explicit new-file whitespace,
JSON validity, source-metadata equality and before/after inventory checks passed.

The repository autouse network-denial fixture applied. Tests validate software
behavior using artificial isolated fixtures; no fixture was counted as evidence.
No new tests or software changes were warranted for this documentation milestone.
External API/network calls: **zero**. No acquisition, endpoint/rights probing,
paid APIs, source mutation, production access, secrets access, service/database
changes, refresh enablement, broker action, deployment or direct Git staging/
commit/reset/restore/checkout was performed. Historical source URLs were treated
as metadata and never opened.

## Exhaustion decision and commit handoff

All 14 retained directories (including the nested public-evidence directory),
root derived reports and relevant canonical contracts have been reconciled.
Local qualification and the approved software regression exhaust authorized local
validation. Real M8/US8/OOS/holdout/forward runs cannot proceed without inventing
inputs; they were not run. No further local admission is defensible.

Evidence assessment: **LOCAL_SCOPE_REACHED**. **EMPIRICAL_READY: NO**.
**LIVE_READY: NO**. Administrative closure awaits supervisor commit and a final
read-only verification of clean HEAD, committed paths and unchanged retained
bytes. No future commit or clean final tree is claimed here.

The commit request uses the existing bridge schema (`message`, `paths`) and
message `docs: record phase two local evidence validation`, with exactly:

- `docs/EVIDENCE_VALIDATION_READINESS.md`
- `docs/evidence-validation/local-audits.json`
- `docs/evidence-validation/retained-inventory.json`
- `docs/evidence-validation/retained-manifests.json`

Phase-2 commits observed before this request: none. The worktree contains only
these four documentation paths. All 67 retained files (129,416,738 bytes) were
rehashed against both the preserved inventory and initial Phase-2 inventory;
manifest metadata and hashes match original retained bytes. The three JSON
artifacts remain unchanged during this handoff reconciliation. Initial ad hoc
checks used the wrong manifest-relative root and compared the current audit to
an older state audit; corrected scope checks passed. Neither check indicated
source mutation or a product defect.

After the supervisor commit, verify its exact four paths, clean branch/HEAD and
unchanged evidence, then publish the final Phase-2 report and DONE. Do not reopen
completed software or reacquire evidence to extend the phase.

Approved log SHA256 `phase2-approved-local-socket-tests.log`: `f62856bf55710418544da7bb61bc3570a1a293c4053b2e85426b8068f0b37ac6`.

Approved log SHA256 `phase2-approved-full-suite.log`: `0e3bfd8e1cf8da4e3069bc63ecab1fffbb59fc63bf6e7ae72d7f774f3dc4936f`.
