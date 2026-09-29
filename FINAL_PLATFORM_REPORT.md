# Platform Report — EGX + US Paper/Shadow Trading Platform

Status as of 2026-09-29 (cycle 152). This is a current-state report, not a
claim of completion. PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED.

- Branch: `agent/er1c-free-acquisition`
- Code HEAD at time of writing: `13465dd` (the commit that updates this report
  follows it; see `git log`)
- Checkpoint details: `PROGRESS.json`

## Start the UI

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

UI: http://127.0.0.1:8000/ . With no environment configured every view starts
and reports unavailable evidence as UNKNOWN/null. Optional read-only inputs
(`EGX_PAPER_RUNTIME`, `EGX_SCAN_HISTORY_PATH`, `EGX_SCAN_LEDGER_PATH`,
`EGX_SCHEDULER_HEARTBEAT_PATH`, `EGX_SHADOW_DIRECTORY`, `EGX_BUILD_REVISION`)
are documented in `README.md`. Container build: `docker compose up --build`.

## API entry points

| Route | Purpose |
| --- | --- |
| `/?section=<S>&market=<M>` | Product shell. S: TODAY, LIVE, PRE-SURGE, SWING, PERFORMANCE, RESEARCH, SYSTEM; M: EGX, US, ALL |
| `/api/product` | Product shell JSON (same parameters; invalid values return 422) |
| `/system`, `/api/system` | Operator state: receipts, providers, scan runs, heartbeat, checkpoint |
| `/shadow`, `/api/shadow` | Audited Paper/Shadow collection |
| `/performance` | Performance, only from authentic lifecycle evidence |
| `/api/today`, `/api/paper-operational` | Compatibility endpoints |
| `/health` | Liveness only |

## Completed capabilities (evidence-backed)

- Unified read-only product shell with all 7 sections x 3 markets; SYSTEM
  renders escaped tables only (no raw JSON), including the project checkpoint.
- EGX operational receipts: hash-verified PAPER_SIGNAL_VERIFIED receipts feed
  TODAY/SWING/LIVE; expired receipts show DATA_STALE; incomplete receipts render
  UNKNOWN fields instead of failing.
- RESEARCH: execution-free sourced notes (SOURCE_FACT / DERIVED_METRIC /
  MODEL_INTERPRETATION / UNKNOWN, with provenance and timestamps; lookahead
  rejected) built deterministically from verified EGX receipts.
- Scheduler: OBSERVE mode by default; opt-in local EGX scan dispatch
  (`EGX_SCAN_MODE=local`) with durable ledger claims, primary/fallback windows,
  guarded completion and schema-v3 scan history; optional read-only ledger
  reconciliation; heartbeat surfaced in SYSTEM.
- Scan scope (cycle 40): opt-in `EGX_SCAN_SCOPE=security_master` requests every
  stored EQUITY ticker (312 in the local research copy), overlaying explicit
  launch evidence where supplied; everything else is classified
  EVIDENCE_BLOCKED, never scanned. Default explicit scope unchanged.
- Explicit acquisition configuration boundary with immutable per-window quota
  cost contracts; default free-provider factory deliberately fails closed.
- US: retrospective point-in-time research contracts (identity, sessions,
  daily/intraday bars, corporate actions, universe snapshots, replay,
  validation). US operational views remain UNKNOWN.

## Test evidence

- Full dependency-backed suite, cycle 152, on commit `261387f`:
  **3235 passed, 667 subtests passed, 0 failed** (773 s). Log preserved at
  `/home/egx-agent/er1-autopilot/state/cycle152-full-suite-261387f.log`.
  `13465dd` (intraday stop/entry guard) then ran the 34 test files that
  reference strategies/intraday: 668 passed. The full suite was not re-run
  for that commit.

- Full dependency-backed suite, cycle 120, on commit `bb03063`:
  **3207 passed, 665 subtests passed, 0 failed** (685 s). Log preserved at
  `/home/egx-agent/er1-autopilot/state/cycle120-full-suite.log`.
- Full dependency-backed suite, cycle 40, on commit `eb65800`:
  **3145 passed, 653 subtests passed, 0 failed** (709 s). Log preserved at
  `/home/egx-agent/er1-autopilot/state/cycle40-full-suite.log`.
- `d681291` (security master scan scope): scan/scheduler/security-master/
  worker/heartbeat/system suites 154 passed, 305 subtests passed. The full
  suite was not re-run for this commit.
- These results come from offline unit tests and in-process/SQLite
  integration tests.

## Runtime validation (local only)

Cycle 152 at `261387f`, uvicorn on 127.0.0.1:8766, unconfigured: all 21
section x market pages and 21 `/api/product` calls returned 200, as did `/`,
`/system`, `/api/system`, `/shadow`, `/api/shadow`, `/performance`,
`/api/today`, `/api/paper-operational` and `/health`. Invalid or lowercase
section/market returned 422. `/api/system` reported live_money false, scheduler
UNKNOWN (no heartbeat configured), US UNKNOWN, EGX NOT_READY.

Cycle 120 at `bb03063`, uvicorn on 127.0.0.1:8765, unconfigured: all 21
section x market pages and 21 `/api/product` calls returned 200, as did `/`,
`/system`, `/api/system`, `/performance` and `/health`. Invalid or lowercase
section/market returned 422. `/api/product` TODAY/EGX reports NOT_READY with
null coverage (no fabricated counts).

Cycle 40 at `d681291`, uvicorn on 127.0.0.1:
- Unconfigured: all 21 section x market pages and 21 `/api/product` calls
  returned 200, as did `/system`, `/api/system`, `/shadow`, `/api/shadow`,
  `/performance`, `/api/today`, `/api/paper-operational` and `/health`.
  Invalid section/market returned 422. Payloads contain null/UNKNOWN, not
  fabricated counts.
- `EGX_PAPER_RUNTIME` pointed at a /tmp copy of the research paper-shadow
  runtime: same 200/422 results. EGX shows 1 observed symbol (COMI, SWING)
  as DATA_STALE (receipt valid until 2026-09-27T07:00Z; last verified
  session 2026-09-24).

Not validated: deployed runtime, scheduled worker runs, live market sessions.

## Cycle 152 review fixes

- TODAY dashboard: "Validated Symbols" counted per-snapshot artifact rows
  rather than distinct symbols. It now counts distinct symbols, and the
  inventory bar counts are HTML-escaped.
- Scheduler heartbeat reader: reads are capped at 64 KiB, and deeply nested
  JSON now fails closed to UNKNOWN. Before, it could raise out of
  `/api/system`.
- Intraday strategies: a READY LONG setup whose stop is not strictly below
  its entry (possible for VWAP pullback, and in degenerate momentum/ORB bars)
  is now NO_CONFIRMATION with no entry or stop.

## Financial Services integration

anthropics/financial-services is pinned at `574ed36` (Apache-2.0) and vendored
read-only under research-data. It is not installed. Status:
METHODOLOGY_REFERENCE_ONLY, layer RESEARCH_ONLY, execution_authority NONE.
All 12 MCP connectors fail closed because they are credentialed commercial
services with no entitlement. The upstream financial-analysis `.mcp.json` is
invalid JSON. EGX coverage is UNKNOWN. Details:
`docs/FINANCIAL_SERVICES_EVALUATION.md`. The status is exposed in RESEARCH and
`/api/product?section=RESEARCH`.

## Security boundaries

- LIVE_MONEY disabled. No broker, exchange or order-placement client exists
  in `app/`. "Fills" are simulated Paper/Shadow accounting only.
- No language-model SDK is imported by the application. Research notes are
  built deterministically and carry no order, side, quantity or fill fields.
- Scheduler `execution_enabled` gates data-refresh work only, never trading.
- Invalid configuration, secrets-bearing errors and unsupported modes fail
  closed, and only exception types are recorded.
- Production paths (`/opt/...`, docker socket) and secrets were not accessed.

## Unsupported / UNKNOWN capabilities

- US operational universe, data readiness, scanning and candidates: no
  configured universe or admitted data source.
- Authoritative dated EGX universe membership: the security master is scope
  attribution only. The 224 baseline target is retained.
- Performance: no authentic lifecycle observation source is connected.
- Provider health and current market coverage.

## Remaining blockers and manual actions

1. Deployment: there is no safe authorized deployment mechanism. Deployment
   and runtime validation are pending.
2. Scheduled free acquisition: no reviewed free daily EGX transport is
   admitted, so fresh receipts cannot be produced automatically. Without them,
   receipts go stale, as observed above. This needs a reviewed source
   admission and real dated cost evidence.
3. Per-symbol launch evidence beyond the existing five-symbol launch scope is
   needed before the security master scope can scan more than a handful of
   symbols.
4. US: an operator must choose and license a US universe/data source.
5. Financial Services connectors need operator-provided entitlements if
   they are ever wanted. Even then they would remain research-only.
6. Activating `EGX_SCAN_MODE=local` (and optionally `EGX_SCAN_SCOPE`) in the
   deployed worker requires integration validation first.
