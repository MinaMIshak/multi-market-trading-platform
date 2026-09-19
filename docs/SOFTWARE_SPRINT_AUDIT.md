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
