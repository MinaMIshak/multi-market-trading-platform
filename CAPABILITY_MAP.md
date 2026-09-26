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
| EGX broad operational scanner (M4) | PARTIAL | app/egx_scan.py coordinates explicit scope through existing non-publishing verifier, isolates failures and counts only accepted results; coordinator/history/SYSTEM/heartbeat suite: 16 stdlib regressions pass. Atomic optional last-run summaries are visible in SYSTEM as historical scope, never current readiness. History admission rejects boolean/fractional counts and schema versions plus untrimmed identities; 29 related stdlib regressions pass. Scanner now binds returned verification symbol/market to requested scope; real launch identity regression awaits dependencies. Launch remains restricted to five symbols; no real broad run claimed | Explicit config loader added (strict existing launch decoder; invalid inputs remain blocked); Compose history path shared. Opt-in scheduler invocation now wired (disabled by default); dated scope and runtime integration still pending; actual decoder/verifier integration test pending dependencies |
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
