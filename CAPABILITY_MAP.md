# Capability map

Audited source: `829676d`, 2026-09-26. See `PROJECT_AUDIT.md` for evidence and
limits. Update individual rows when new evidence warrants it; do not repeat the
whole audit. Mission milestones here differ from historical research milestones.

WORKING means the explicitly bounded component is implemented with regression
coverage and prior validation or observed runtime evidence. It never means the
whole product is operational. Current pytest rerun is pending dependency access.
PARTIAL means useful implemented scope with missing integration/coverage; LEGACY
means retained compatibility; NOT_OPERATIONAL means present but not operating as
the required product; MISSING means no implementation found; BLOCKED identifies
a specific unavailable mandatory input, not a global mission stop.

| Capability | Classification | Evidence / current boundary | Next required work |
| --- | --- | --- | --- |
| M1 architectural audit | WORKING | PROJECT_AUDIT.md, module inventory, source/tests/history and isolated runtime observation | Preserve; targeted updates only |
| Raw/canonical daily and index data | WORKING | app/data stores/pipelines and corresponding tests; COMI artifacts observed | Reuse for broad ingestion |
| Source review, PIT and actions | WORKING | point_in_time.py, reference.py, historical evidence/PIT, M3/R1 tests | Preserve explicit-selection vs dated-universe distinction |
| Authoritative current EGX universe | BLOCKED | 224 baseline target; isolated master has 312 undated equities, not dated listing evidence | Establish authoritative current dated membership |
| EGX free daily acquisition | PARTIAL | Accepted COMI TradingView receipt; default repository runtime remains EODHD | Integrate reusable lawful free source and fallback without weakening admission |
| Free-source coverage/rights/health | PARTIAL | Historical qualifications and source-bound review exist; no autonomous health/fallback integration | Reuse prior findings, expose precise failures and rights limits |
| EGX calendar software | WORKING | app/core calendar/evidence/promotion modules and calendar tests | Maintain actual dated evidence and expose freshness |
| EGX manual SWING verification | WORKING | swing_launch.py, launch/UI tests; one accepted READY_NO_SIGNAL receipt | Preserve source binding, 260 bars and no-fill semantics |
| EGX broad operational scanner (M4) | PARTIAL | app/egx_scan.py coordinates explicit scope through existing non-publishing verifier, isolates failures and counts only accepted results; coordinator/history/SYSTEM/heartbeat suite: 16 stdlib regressions pass. Atomic optional last-run summaries are visible in SYSTEM as historical scope, never current readiness. History admission rejects boolean/fractional counts and schema versions plus untrimmed identities; 29 related stdlib regressions pass. Scanner now binds returned verification symbol/market to requested scope; real launch identity regression awaits dependencies. Launch verification now accepts canonical lexical symbols with all existing admission gates; refresh remains restricted to existing mappings; real launch integration pending and no real broad run claimed | Explicit config loader added (strict existing launch decoder; invalid inputs remain blocked); Compose history path shared. Opt-in scheduler invocation now wired (disabled by default); dated scope and runtime integration still pending; actual decoder/verifier integration test pending dependencies |
| EGX scan data-failure classification | WORKING | Coordinator preserves verifier DATA_STALE and maps DATA_INSUFFICIENT to NOT_READY; both remain unscanned. SYSTEM history schema v2 retains v1 compatibility. 36 related stdlib tests pass; real launch adapter test pending missing pytest/runtime dependencies | Validate real runtime adapter and scheduled invocation when dependencies are available |
| Valid operational candidate ranking (M5) | MISSING | PRE_SURGE ranking is legacy research scoring only | Rank only independently valid, risk-admitted setups |
| Intraday patterns | NOT_OPERATIONAL | FIRST15/ORB/VWAP/momentum engines and test_m4_engines.py | Require market-specific data and empirical validation before wiring |
| PRE_SURGE scorer | LEGACY | Versioned V5 percentile parity; no trained V7 artifact | Retain research boundary; no operational promotion |
| Market regime / relative strength / sector context | MISSING | Regime is supplied to risk; no operational context producer found | Evidence-backed context only when justified |
| Volatility/structural stops | PARTIAL | Trailing-low research stops; operational Q03 fixed −3%/+6% | Validate alternatives before altering frozen rule |
| Risk admission/sizing | WORKING | app/risk; M5 tests; time/state/regime/cash/exposure/liquidity caps | Supply authenticated operational snapshots |
| Operational SWING sizing/liquidity | NOT_OPERATIONAL | WATCH is explicitly unsized with risk NOT_EVALUATED | Integrate actual liquidity and risk admission |
| Sector/correlation exposure | PARTIAL | Explicit group cap and portfolio policy; no statistical correlation producer | Authenticated group/sector membership and justified policy |
| U.S. identity/universe/sessions/actions/PIT | WORKING | app/us contracts/admission modules, US0–US5B tests | Preserve USD/MIC/New York boundaries |
| Actual U.S. operational universe | MISSING | No configured product universe found; pilot tickers not a universe | Establish dated scope and source identity, report actual count |
| U.S. research SWING/planning/risk/replay | WORKING | US6–US8 modules and tests; separate research contracts | Preserve; do not infer operational scanning |
| U.S. broad scanner/free acquisition (M6) | MISSING | No operational multi-symbol source-to-scan integration | Add U.S.-specific operational composition |
| Forward Paper/Shadow lifecycle | WORKING | app/paper/shadow_*; lifecycle/tamper/chronology tests | Automate admitted facts and events, never infer fills |
| Capital reservations/settlements/marks | WORKING | shadow_allocations/portfolio/daily modules and tests | Provide authentic operational currency-separated inputs |
| Performance engine/replay | WORKING | app/performance and M7/M7.1 tests | Keep canonical lifecycle evidence boundary |
| Operational performance report (M7) | BLOCKED | Main route passes no report; canonical trade observation source absent | Admit genuine lifecycle observations; retain sample limits |
| Research walk-forward/holdout/sensitivity | WORKING | research and US8 validation modules/tests | Run only on admissible evidence |
| Empirical strategy support | BLOCKED | Existing evidence/readiness findings; no new real OOS/forward sample verified | Obtain lawful admitted evidence; no fixture promotion |
| Operational receipt API/UI | WORKING | main.py, ui/operational.py, test_operational_ui.py; isolated reader accepted COMI | Preserve fail-closed expiry and source binding; malformed receipts now block only their symbol, with PARTIAL aggregate state when valid neighbors remain. Database failures still block the whole view. Stdlib regression covers malformed documents, audit IDs, identities and timestamps; FastAPI regression updated but pending dependencies |
| Explainable stock reports | PARTIAL | State/source/session/window and WATCH plan; no complete explanation pipeline | Expose evaluated gates, reasons, confirmation/invalidation |
| Unified EGX/US/ALL dashboard | PARTIAL | Active TODAY/Shadow/Performance; remaining tabs largely labels | Connect market-specific operational views |
| SYSTEM progress UI (M2) | PARTIAL | Read-only /system and /api/system implemented; 4 stdlib regressions pass for scoped receipt counts, expiry, missing/corrupt evidence and escaping | FastAPI integration validation and safe deployment pending; checkpoint now packaged and explicit build argument wired; optional expiring worker heartbeat implemented with 3 stdlib regressions; Compose shared-path wiring and separate-process writer/reader regression now pass; full worker integration and deployed build identity still pending |
| Scheduler components | WORKING | EGX worker, orchestration, claims/recovery and dispatch tests | Reuse durable ledger |
| Autonomous both-market operation (M8) | NOT_OPERATIONAL | Compose defaults OBSERVE; no strategy/lifecycle/both-market schedule | Opt-in local EGX scan dispatch added with verified-calendar gates and durable ledger/fallback; malformed completion counts now fail before ledger success (27 related stdlib regressions pass). SQLite runtime integration attempted but blocked by missing pydantic. Free refresh and U.S. scheduling remain missing |
| Deployment/release verification | PARTIAL | Docker/Compose exists; baseline deployment operator-reported only | Use safe deployment mechanism; pre/post validation and rollback |
| Experimental alternate app | LEGACY | app/experimental.py and snapshot/runtime tools | Preserve compatibility; active app remains app.main |
| Current full-suite execution | BLOCKED | pytest/dependencies absent; package-host DNS failed | Provision authorized offline wheels or restore package access |

## Count semantics for M2

Counts must carry market, observed time, source, scope and expiry. Keep null for
unknown; zero requires an observed complete scope. Keep the 224 baseline target
separate from an authoritative universe count. Never count security-master rows,
artifacts, or UI labels as scanned symbols. Expired receipts cannot contribute
to current ready/scanned/candidate totals. An isolated receipt count is not a
full-universe scan run. U.S. fields remain unknown until actual scope is supplied.
Expose scheduler state independently from API health. A source-control HEAD or
checkpoint revision is not the deployed build revision.

## Incremental checkpoint: scan coverage blockers

Explicit scan configuration now retains safe diagnostic codes for invalid launch
contracts and symbol mismatches. The scheduler forwards these to the coordinator,
which preserves EVIDENCE_BLOCKED and zero scanned contribution for rejected inputs.
Missing inputs remain distinguishable; raw decoder details are never reported.
22 focused stdlib scanner/configuration/dispatcher/history regressions pass.
No authoritative membership, newly ready symbols or runtime scans were established.

## Incremental checkpoint: explicit scan scope identity

Configuration, coordinator and historical reporting now share the existing
UniverseMember lexical identity rules. Bare strings, mappings and unordered sets
cannot become scanner scope; malformed symbols are rejected before verification.
A synthetic 224-symbol regression preserves every requested member as blocked
with zero scans; it is capacity testing, not actual EGX membership or coverage.
33 affected stdlib regressions pass. Five-symbol launch restriction, authoritative
membership evidence, runtime dependencies and deployment remain unresolved.

## Incremental checkpoint: launch coverage diagnostics

Explicit configuration now distinguishes supplied inputs outside the supported
five-symbol launch contract from malformed evidence. Unsupported symbols remain
in scope as EVIDENCE_BLOCKED with zero scanned contribution. The configuration
and launch model share one Literal contract. Refresh target expansion and dated
membership evidence remain pending; no market coverage increase is claimed.
37 affected stdlib tests pass; real launch integration awaits dependencies.

## Incremental checkpoint: equity identity admission

The shared EGX launch identity gate now also requires security-master EQUITY
classification before refresh or signal preparation. Matching ticker and UUID
alone previously admitted INDEX/UNKNOWN rows. Added launch rejection regressions
for both types; these remain unexecuted because pytest/runtime dependencies are
unavailable. The 37 stdlib scanner and visibility regressions pass. This guard
does not establish dated membership or increase coverage; broad scope expansion
and real launch integration remain pending.

## Incremental checkpoint: independently validated identity admission

The launch equity identity gate now delegates to a dependency-free production
predicate. Four regression tests cover matching UUID/text identities, wrong or
missing identity fields, INDEX/UNKNOWN/missing classification and malformed
master rows. Missing fields now produce reviewed admission failures rather than
uncaught key errors. All 41 affected stdlib tests pass; actual launch integration
and the full suite still require unavailable pytest/pydantic dependencies.
Package installation retry returned no matching distributions. No new universe,
data readiness, scan execution or deployment is claimed.


## Incremental checkpoint: broadened explicit verification scope

Removed the five-symbol verification allowlist. The launch model now validates
canonical symbol spelling using the same predicate as scanner scope; identity,
EQUITY classification, calendar, reviewed source and PIT admission still apply.
Refresh retains existing explicit provider mappings and returns EVIDENCE_BLOCKED
before runtime/provider invocation when no mapping exists. It never manufactures
a provider symbol from a ticker. This expands software input scope, not verified
market coverage or authoritative membership.

42 dependency-free regressions pass, including additional-symbol decoding and
blocked evidence accounting. New real launch tests cover transport, missing
identity, missing history and unmapped refresh without provider calls; these are
pending pytest/pydantic availability. Syntax checks and git diff --check pass.
No deployment or fresh market observation occurred. Broad refresh integration,
authoritative dated membership and actual scheduled scans remain pending.

## Incremental checkpoint: preserve admitted scan history

The history writer and SYSTEM reader now share the existing summary admission
contract. Invalid counts, identities, market, safety state or scope are rejected
before creating a temporary file or replacing the previous admitted run. Valid
replacement and legacy schema reads remain supported. This prevents malformed
producer output from destroying the last usable operator-visible scan summary.

44 affected dependency-free regressions pass, including ten invalid-write cases
and successful replacement. Runtime launch integration and full release testing
remain pending unavailable pytest/pydantic; isolated package installation returned
no matching distributions. No new scans, market data or deployment are claimed.

## Incremental checkpoint: explicit refresh alias admission

`refresh_once` now accepts an optional explicit `DailyRefreshTarget`. Before
runtime construction, its provider-specific registered alias must resolve to the
requested equity UUID and ticker. Missing, ambiguous, cross-provider and wrong
instrument mappings fail closed. Existing default targets remain compatible;
provider symbols are never generated from tickers. Alias registration does not
establish licensing, source review, dated membership or usable history; existing
calendar, quota, canonical admission and subsequent signal gates remain required.

49 dependency-free regressions pass. Two added real launch integration tests and
full release validation remain pending missing pytest/pydantic. This is a Python
composition capability only: CLI configuration, reviewed free-provider composition,
authoritative membership and broad operational refresh remain pending. No market
observations, source rights, scans or deployment are newly claimed.

## Incremental checkpoint: explicit refresh CLI mapping

The local refresh CLI accepts `--provider-symbol` for an explicitly registered
EODHD alias. Configuration validates the requested equity identity before token
access, then forwards a `DailyRefreshTarget` into the existing runtime admission.
The option is rejected on other operations; omission preserves default targets.
No provider symbol is inferred and no source rights are implied by alias registration.
This extends the existing EODHD configuration path only; reviewed free-provider
composition remains pending and zero-paid-subscription operation is not claimed.

53 dependency-free regressions pass, including four new configuration checks.
Two CLI regressions were added but execution remains pending missing pytest and
pydantic; syntax validation passes. No fresh market observations, scans or deployment.

## Incremental checkpoint: default refresh alias admission

Default EODHD target selection now passes the same provider-specific alias and
equity identity gate as explicit targets before refresh runtime construction.
An injected provider cannot use a default code unless that alias is registered
for that provider and requested equity. Registration still does not prove source
rights, dated membership, or operational readiness.

53 dependency-free regressions and launch syntax checks pass. New launch
regressions reject missing/unregistered/canonical provider namespaces before
runtime construction; execution awaits pytest/pydantic. The alternate-provider
engineering fixture now explicitly registers its alias. No deployment, provider
fetch, fresh market observation, or expanded coverage is claimed.

## Incremental checkpoint: refresh count contracts

Refresh admission now requires a strictly positive integer history minimum and
a nonnegative integer provider record count. Boolean minima previously reduced
the required history to one bar; NaN minima bypassed the length comparison.
Boolean and floating provider counts could compare equal to payload lengths.
These malformed inputs now fail closed before payload canonicalization.
Added 15 parametrized regression cases; execution awaits pytest/pydantic.
48 existing dependency-free scope/scan/heartbeat regressions and changed-file
syntax checks pass. No new market observation or deployment is claimed.

## Incremental checkpoint: scheduler heartbeat contract

SYSTEM rejects boolean and fractional heartbeat schema versions. The optional
writer validates supported modes, positive integer polling intervals and aware
timestamps before creating a temporary file or replacing previous evidence.
Malformed input preserves the last valid heartbeat, which still expires normally.
54 dependency-free heartbeat/SYSTEM/scope/mapping/scanner/configuration/dispatcher/
history tests pass, including separate-process reader/writer coverage. No runtime
scheduler operation, deployment or current market coverage is inferred.

## Incremental checkpoint: refresh identity UUID admission

Shared EGX equity identity admission now rejects matching malformed identifiers
and arbitrary stringifiable objects before explicit refresh aliases can be
authorized. Valid UUID objects and matching stored UUID text remain supported;
exact identity comparison is preserved. This is a local admission capability,
not dated membership, provider rights, acquisition or market scan evidence.
37 dependency-free identity/mapping/scanner/configuration/heartbeat regressions
pass. Dependency-backed integration remains pending: installing pytest/pydantic
failed because package-host DNS was unavailable. No deployment or fresh runtime
market observation occurred. Reviewed free-provider composition remains pending.

## Incremental checkpoint: provider finite-value admission

Existing EODHD acquisition now rejects NaN and positive/negative infinity in
OHLC, adjusted close and volume before returning a provider response. Finite zero
volume remains valid. Nineteen isolated checks of the actual AST-loaded validator
and 25 dependency-free mapping/scanner/heartbeat regressions pass. Nineteen added
provider regression cases await pytest/pydantic; this does not establish integration
validation, free-source readiness, fresh data, scans or deployment.

## Incremental checkpoint: refresh result accounting

Daily refresh job reporting no longer coerces counts with `int()`. Returned raw,
valid and quarantined counts must be nonnegative integers and reconcile before
the result contributes to completed items. A failure retains only earlier valid
items. This is a reporting guard; it does not roll back artifacts already produced
by the canonical pipeline or establish new provider admission.

24 focused offline regressions pass, including four tests loading the actual
dependency-free job module, malformed-count subcases, partial completion and
mapping/scanner coverage. Dependency-backed integration remains pending missing
pytest/runtime dependencies. No fresh market evidence, scans or deployment.

## Incremental checkpoint: reject counts before promotion

Daily refresh now rejects malformed ingestion record counts before invoking
canonical promotion, which can persist artifacts. Post-promotion manifest count
reconciliation remains in place. Six offline actual-job regressions include
proof that malformed counts never invoke promotion and that a later failure
preserves only the preceding valid promotion; ten mapping and ten scanner
regressions also pass. These are engineering fixtures, not acquisition or market
coverage evidence. Free-provider composition and runtime validation remain pending.

## Incremental checkpoint: bind refresh ingestion identity

The daily refresh job checks returned canonical/provider symbols and requested
start/end/snapshot dates against its target before canonical promotion. Mismatched
or missing fields fail closed; later failures preserve only preceding completed
targets. This prevents a returned ingestion for another symbol or window from
being promoted and reported as the requested refresh. Raw ingestion may already
have persisted evidence; the guard does not roll it back or establish source rights.

28 dependency-free regressions pass, including ten identity/window rejection
subcases and partial completion. Existing job fixtures now include the real
ingestion identity fields. Dependency-backed integration remains pending: pytest
installation returned no matching distribution. No fresh coverage, provider
acquisition, scheduled operation or deployment is claimed.
