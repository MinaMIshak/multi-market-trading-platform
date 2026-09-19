# Software sprint forensic baseline

Audited checkout: `agent/er1c-free-acquisition`, HEAD
`0d05914288ce3f5f11612388e9db5de9e1d59f8b`; user `egx-agent`; initial tree clean.
Repository: `/home/egx-agent/work/egx-trading-platform-us`.
The current mission explicitly supersedes the older AGENTS workspace/branch.
STATE.md's early HEAD references are historical; its reconciliation section and
Git history agree with this checkout. Preserve the rollback of readiness-only UI.

## Architecture reconstructed from repository code, tests and documents

| Boundary | Existing implementation and separation |
| --- | --- |
| M0/M1 | Offline regression harness; core calendar truth, maintenance and scheduler execution context; observe default, disabled maintenance default |
| M2 | `app/data/quota.py` and daily-refresh admission; explicit verified costs and shared budget |
| M3 | Immutable raw/canonical storage, reference and security identity, quality and operational PIT admission |
| M4 | `app/strategies`: Swing, Pre-Surge and intraday engines over admitted inputs |
| M5 | `app/risk`: explicit policy, cash/exposure/quantity constraints before execution |
| M6 | `app/paper/simulator.py` and replay contracts: canonical simulated execution |
| M7 | `app/performance`: deterministic paper performance; existing renderer consumes canonical reports |
| R1/ER1 | Separate historical availability/review/PIT contracts and bounded acquisition audits; operational M3 cannot substitute for retrospective admission |
| US | Independent identity/session/daily/action/universe contracts, retrospective composition, intraday/replay, planning and research adapters |
| Shadow | Frozen watchlists, facts, triggers, fills, positions, exits, continuation, shared capital ledger, audited native portfolio snapshots/series |
| M8/US8 | Research protocol, purged partitions, OOS, frozen holdout and confidence evaluation; neither UI nor shadow makes validation decisions |

The shadow fixed-period bridge supplies authenticated series to M7 without
replacing trade economics or M8 evidence semantics. Native portfolio accounting
does not establish admissible EGP/USD conversion. Existing artificial tests do
not supply authentic market evidence.

## Runtime topology and staging boundary

The repository defines one FastAPI app, server-rendered HTML (no separate frontend
build), SQLite read-only TODAY queries, `/api/today`, `/performance` and `/health`.
The performance route currently supplies no report. The root route shows canonical
data inventory, not the implemented shadow lifecycle. Thus usable shadow exposure
is a real integration gap even though the core accounting already exists.

Declared dependencies are FastAPI and Uvicorn; application contracts also use
Pydantic. The shared interpreter is read/execute only. Dockerfile runs Uvicorn on
8000; repository Compose defines an API and a separate scheduler, shared data
volume and scheduler secret mount. Those mechanisms cannot be reused unchanged
for this mission. No Docker command, service inspection, secret read or port-8000
request was performed. Repository configuration is not proof of active runtime
state. No existing isolated staging mechanism was found among tracked files.

Staging is not established in this milestone. Before starting it, complete a
bounded inspection of permitted experimental runtime ownership and select a free
non-8000 loopback port. Use a committed immutable source snapshot, separate state,
no scheduler/provider credentials, and the existing FastAPI surface. Record its
commit identity and label all experimental pages EXPERIMENTAL / PAPER ONLY.
Do not promote the present uncommitted change: the supervisor commit bridge must
finish first. A tested candidate must pass health/build-identity checks before
replacing a known-good experimental process; failure must leave that process and
its state intact. No claim of continuous staging availability is made here.

## First concrete defect and fix

TODAY published symbols before subsequent queries and integrity verification
completed. A missing downstream table therefore returned unavailable state with
apparently validated symbols still visible. Separate autocommit reads could also
mix data versions during concurrent ingestion. The reader now uses a single
read transaction, publishes only a complete integrity-checked snapshot, closes
its connection on both success and failure, and encodes database paths as URIs
without treating filename characters as URI options.

Regression fixtures cover a late query failure, concurrent WAL writer, failed
integrity check/connection closure and URI-significant filename characters. All
are artificial isolated database tests, not market evidence.

## Shortest defensible remaining path

1. Commit this verified read-boundary fix through the supervisor. Establish the
   isolated experimental runtime from committed code with build mapping and
   last-known-good preservation; avoid restoring reverted readiness dashboards.
2. Wire existing authenticated shadow readers into the real app through a bounded
   read-only input adapter. Require upstream evidence packages and re-audit;
   never trust arbitrary report JSON or invent an empty frozen watchlist.
3. Qualify genuinely new free evidence paths and independent review. Existing US
   pilot gaps remain historical availability, exact-date identity/universe,
   every-date sessions and complete bounded actions. Do not repeat closed probes.
4. Assess stock/ETF and horizon support against actual contracts and evidence
   before implementing adapters. No ETF comparison, verified IBKR cost schedule,
   validated holding horizon, macro feature or probability is established here.
5. Run admitted packages through existing replay, OOS/holdout and forward paths
   when evidence permits. Software completion must remain distinct from empirical
   completion and sustained forward sample accumulation.

The sprint is not complete. Historical admission remains NO_GO; empirical edge
and real-money readiness are NOT YET VALIDATED. No authentic sessions, prices,
recommendations, fills or results were created or reconstructed.

## Current UI override — 2026-09-19

Resume HEAD `31697a85b056fd98ab2d9a84cf8ee2785159e9a9`, expected branch,
user egx-agent. Preserved unfinished `shadow.py`, `shadow_input.py` and adapter
tests. Prior experimental runtime milestones are historical/test infrastructure.
The current directive supersedes the staging steps above: no additional runtime
or deployment work is on the critical path. Existing `app.main:app` remains the
product application, with its existing TODAY and M7 structure.

Integrated read-only `/shadow` and `/api/shadow` into that application and added
a PAPER CANDIDATES navigation link. `EGX_SHADOW_DIRECTORY` must explicitly name
an absolute operator-owned directory; absent configuration returns unavailable
without creating state or inferring a path from the application database.
The fixed `input.json` envelope uses schema `shadow-ui-input-v1`, with `watchlist`
and `evidence_packages` containing complete canonical model JSON fields. Decimal
values are strings and timestamps must explicitly express UTC. The bounded
4 MiB regular-file reader rejects duplicate fields, noncanonical types and links.
Transport parsing never substitutes for the upstream freeze/evidence/ledger audit,
which runs on every read. No upload, evidence approval, freeze or execution action
is exposed by these routes. Operator-owned files remain a trust boundary; concurrent
malicious mutation/hardlinks are not prevented by these checks.

An audited frozen record does not prove freshness: both page and API explicitly
say freshness is not established. Candidates remain distinct from fills; current
positions, NAV and validated performance are not inferred. Historical candidate
records may be displayed with their original date and cutoffs. No authentic input
has been configured or made visible in a running application by this milestone.

Next critical path: adapt existing authenticated execution/portfolio readers into
the same application, preserving M7 and M8 boundaries, then assess stock/ETF,
horizon and dated execution-economic contracts against actual evidence. Historical
PIT/availability/universe/session/action/review limitations remain NO_GO; software
integration can proceed without claiming empirical validation or LIVE readiness.

## Existing UI execution observation integration — 2026-09-19

The same `/shadow` and `/api/shadow` routes now optionally read `execution.json`
from the explicitly configured shadow directory. Schema `shadow-ui-execution-v1`
requires exact fields: `facts`, `fact_packages`, `fill_policy`, `fill_packages`,
`exit_policy`, `exit_packages`, `evaluation_facts`, `evaluation_fact_packages`.
The last two are explicitly null for the original entry fact evaluation, or
complete canonical later same-session facts/packages. All models include every
canonical field; arrays encode tuples, decimals are strings, clocks express UTC.
The existing bounded regular-file transport applies independently to this file.
No arbitrary report, file locator, NAV or externally supplied P&L is accepted.

The adapter binds execution to the separately audited input watchlist and invokes
`exit_evaluation_view`, reauditing frozen candidates, policy selection, facts,
trigger, fill, entry position and exit receipts. Missing or altered execution
clears only execution results; an independently valid frozen collection can still
be shown. Unsafe linked directory state fails the entire read. Reads create no
state and publish no exception details. Filesystem trust limitations above remain.

The page distinguishes a gross mark as of an observed bar from liquidation P&L;
authenticated CLOSED records expose existing native-currency gross/net arithmetic.
UNKNOWN ordering remains UNKNOWN with no inferred trade or P&L. This is one
same-session observation, not current portfolio state, capital settlement, NAV,
validated performance or proof of freshness. Multi-session continuation and
portfolio readers remain the next integration work. M7/M8 are unchanged. No
runtime was configured, launched, refreshed or deployed and no authentic input
or empirical result was created by this software milestone.

## Existing UI multi-session observations — 2026-09-19

`execution.json` also accepts `shadow-ui-continuation-v1`. It retains the six
required entry facts/fill/exit policy and package fields from the same-session
schema, replacing `evaluation_facts` and `evaluation_fact_packages` with
`continuations` and `continuation_packages`: the complete ordered canonical
ForwardContinuationBundle array and matching nested evidence-package arrays.
Mixing the schemas or supplying incomplete chains fails closed. The same bounded
reader and canonical decoding apply; every read invokes the existing
`continuation_exit_evaluation_view` to re-audit the complete execution ancestry.
No caller-provided computed report or P&L is admitted.

The existing page displays entry-session and continuation evaluations separately.
OPEN exposes only the authenticated as-of gross mark; CLOSED exposes native trade
P&L; UNKNOWN exposes neither inferred marks nor trades. This observation does not
establish current status, freshness, capital settlement, portfolio NAV or empirical
validation. Existing same-session transport remains compatible. Invalid execution
clears the execution view while independently audited candidates remain visible.
No runtime, deployment, evidence acquisition or M7/M8 changes are involved.

Next integration boundary: existing authenticated native portfolio snapshot and
capital-settlement readers, without treating a single trade as a portfolio.
