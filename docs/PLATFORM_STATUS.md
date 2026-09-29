# Platform status and A–X gap scan

As of 2026-09-29, branch `agent/er1c-free-acquisition`. PAPER/SHADOW ONLY.
LIVE_MONEY=DISABLED. This document states what is implemented and verified
in the repository. Anything about the server or public endpoint is operator
reported, because the agent session that wrote this could not reach them.

## Verification boundary

- Repository: full test suite run locally (Python 3.11 venv from
  `requirements-dev.txt`), offline, with the network forbidden in tests.
- Runtime: `uvicorn app.main:app` on 127.0.0.1 against an artificial snapshot
  bundle. `/health`, `/api/product`, `/api/system`, TODAY and `/system` were
  verified: snapshot VERIFIED, readiness EVIDENCE_BLOCKED, live DISABLED.
- Not verified by the agent:
  - the server host: no `/home/egx-agent`, no uid-999 processes and no nginx
    visible from the agent container;
  - `https://3-126-217-243.sslip.io/`: the egress proxy returns 403;
  - `ticker.egidegypt.com` and `beta.egx.com.eg`: the egress proxy returns 403.
- Other branches: `agent/us-market-foundation` and `agent/development` have
  unrelated histories (root "chore: establish clean platform baseline",
  9–11 Sept). A per-file comparison shows they hold only older versions of
  files HEAD has since advanced; for example, the inline bootstrap helpers were
  later refactored into `app/bootstrap_replay.py`. Nothing needs merging.

## Server verification (2026-09-29, host session as `egx-agent`)

A later session running on the host itself observed the following read-only.
Details and procedures are in `docs/PREVIEW_RUNTIME.md`.

- nginx (root) serves `3-126-217-243.sslip.io` with basic auth and proxies to
  127.0.0.1:8001. That port is an `egx-agent` uvicorn with `--reload` running
  from the shared `egx-trading-platform-us` checkout, with no `EGX_*`
  variables. It shows every component as UNAVAILABLE (the default DB path does
  not exist), and every change to that checkout reloads it.
- The uid-999 app on 127.0.0.1:8000 (root Docker) runs build `b0ebc08`, an
  ancestor of this branch. It mounts
  `research-data/paper-shadow-operational` **read-only**. The uid-999
  `scheduler_worker` mounts no `/home/egx-agent` path. Its database and
  heartbeat are not visible to `egx-agent`.
- The operational `platform.db` is owned by `egx-agent`, in WAL mode. No
  process had it open. Contents: 319 instruments, 1262 aliases, 2 daily
  artifacts (COMI, TradingView, EVIDENCE_BLOCKED), 139 session rows through
  2026-09-26, 7 audit events and 1 `PAPER_SIGNAL_VERIFIED` receipt.
- `er1-autopilot/state/runtime-preview/platform-preview.db`, served on 8002, is
  a different database: 318 instruments, 5 daily artifacts, 24 ingestions,
  378 `scheduled_jobs`, no sessions.
- Full suite on the host (Python 3.12 venv) at `99d0f41`: see PROGRESS.json.
- A pinned release plus a verified snapshot was validated on 127.0.0.1:8011.
  The public cutover is pending operator go-ahead.

## Data and readiness flow

```
provider adapter (raw bytes) -> ImmutableRawStore + manifest      [existence]
  -> canonicalize_daily_row / admission policy -> VALIDATED artifact  [validation]
  -> app/data/source_admission.py registry -> ADMITTED | EVIDENCE_BLOCKED [admission]
  -> app/paper/swing_launch.prepare_signal (ADMITTED only) -> receipt  [eligibility]
  -> shadow lifecycle -> M7 performance report                          [evidence]
```

Readers resolve every input through `app/runtime_state.py`: explicit variable,
else the `EGX_RUNTIME_STATE_DIR` snapshot bundle, else the legacy default.
`readiness.EGX` reports components, blockers and tri-state dimensions. It is
never READY, and `overall_operational_ready` stays false while no dated
authoritative universe exists.

## A–X classification

| # | Scope | Status | Evidence / remaining dependency |
|---|---|---|---|
| A | Market data acquisition | PARTIAL-BLOCKED | Provider-neutral `MarketDataProvider`, raw store, EGID security master, official EGX index acquisition. No free per-equity EGX daily source is qualified: EGID history needs auth (401), EODHD is paid, TradingView rights are unreviewed. The cloud agent's egress to EGX hosts was denied. From the host, `beta.egx.com.eg` and `ticker.egidegypt.com` are reachable (2026-09-29): official index bars for 2026-09-27..29 were fetched and rehearsed (refresh + calendar backfill) on a copy only. Applying them to the operational DB awaits operator approval. |
| B | Source admission | COMPLETE | Registry with typed access/entitlement/delay/evidence; exact-identity, fail-closed; gates SWING; shown in TODAY/SWING/LIVE/SYSTEM. No source is admitted (truthful). |
| C | Canonical market data | COMPLETE | OHLC relationships, quarantine, duplicates, ordering, finite/positive checks; `adjusted_close` optional audit-only (`1d58103`); neutral row contract documented. |
| D | Security master | PARTIAL-BLOCKED | EGID identities with provenance, fail-closed refresh (empty/truncated). Dated authoritative membership needs an authoritative dated source (external). |
| E | Calendar and sessions | PARTIAL-BLOCKED | Verified-session freshness (no weekday arithmetic), historical session verification from admitted index evidence. Future holidays are not invented; forward verification needs admitted evidence. |
| F | TODAY | COMPLETE | Identities, daily observations with freshness, source status, readiness components and dimensions, receipts labelled by current admission. |
| G | SWING | PARTIAL-BLOCKED | Deterministic SWING v1 (Q03 plan), PIT, reviewed evidence binding, admission gate, publication. New candidates need an admitted, session-current source. |
| H | LIVE | PARTIAL-BLOCKED | Shows the near-current feed as UNAVAILABLE plus declared source delay, latest market date and freshness; never real-time. Needs an admitted intraday/delayed feed. |
| I | PRE-SURGE | PARTIAL-BLOCKED | `PreSurgeV7Engine` contract exposed (formula, eligibility, version, "not a prediction"). Needs attested scorer rows (no model shipped) and admitted data. |
| J | Candidates and signals | PARTIAL-BLOCKED | Deterministic states WATCH / READY_NO_SIGNAL / NOT_READY / DATA_STALE / EVIDENCE_BLOCKED with reasons and expiry; blocked on admitted data. |
| K | Paper/Shadow | PARTIAL-BLOCKED | Simulator, shadow fills/exits/positions/portfolio/ledger modules with tests; needs authentic candidates to produce lifecycle evidence. |
| L | Performance | PARTIAL-BLOCKED | M7 analyzer; the UI shows UNAVAILABLE without an authenticated report. Never synthesized. |
| M | Research | COMPLETE | `ResearchNote` contract (SOURCE_FACT / DERIVED_METRIC / MODEL_INTERPRETATION / UNKNOWN); receipt-derived notes; financial-services recorded and re-checked. |
| N | SYSTEM | COMPLETE | Build, checkpoint, heartbeat, scan history, receipts, source registry, runtime-state inputs, snapshot verification, readiness dimensions. |
| O | Scheduler | PARTIAL-BLOCKED | Worker, heartbeat, dispatch, scan and acquisition-config boundaries are fail-closed and tested. The running host scheduler is uid 999 in root Docker. It mounts no egx-agent path, so it does not write the operational DB, and its heartbeat/DB are not visible to egx-agent. Changing or observing it needs its owner (ADMIN_REQUIRED). |
| P | Read-only snapshot | COMPLETE | `tools/runtime_state_snapshot.py` (online backup, integrity, hashes, manifest v2, missing list, atomic, no overwrite) plus verification in SYSTEM. |
| Q | API | COMPLETE | `/api/product` and `/api/system` share readiness; end-to-end snapshot test; LIVE_MONEY label protected (`live` key). |
| R | UI | COMPLETE | All seven sections render truthful state; misleading "Admitted source" label removed. |
| S | US market | PARTIAL-BLOCKED | US0–US8 historical contracts in `app/us`, with tests. There is no configured US runtime universe or admitted US data source, so the product shows US as UNKNOWN. |
| T | Configuration | COMPLETE | Central resolver, bundle mode, MIXED_STATE_SOURCES warning; writers stay explicit-only. |
| U | Observability | COMPLETE | Structured, attributable records: raw manifests + `data_ingestions` (acquisition), `audit_events` (PIT validation, receipts), scan history JSON, heartbeat, snapshot manifest. Plus a path-free JSON `api_startup` event (build, runtime-state mode, input origins, snapshot status, warnings, LIVE_MONEY) logged once per process. |
| V | Security | COMPLETE | 2026-09-29 sweep: no shell/eval/pickle; SQL interpolation only of module constants; list-argument subprocesses; read-only GET routes, docs disabled; no tracked secrets. |
| W | Startup/deployment | PARTIAL | `tools/preview_release.py` (immutable, hash-verified release at an exact commit) + snapshot bundle, validated on host loopback 8011; runbook `docs/PREVIEW_RUNTIME.md` with cutover and rollback. The 8001 cutover is pending operator go-ahead. nginx is unchanged. Updating the uid-999 containers is ADMIN_REQUIRED. |
| X | Documentation | COMPLETE | This file, runtime/snapshot, source qualification, financial-services evaluation. |

## External blockers and owners

| Blocker | Owner / action | Unlocks |
|---|---|---|
| No admitted free EGX per-equity daily source | Operator/business: obtain a source with reviewed paper/shadow entitlement (or a written EGID/EGX data agreement), then add a `DailySourceDeclaration` with evidence | G, H (daily), I, J, K, L |
| Operational-DB write of official index/calendar evidence not yet approved | Operator: approve running `official_index_refresh_cli` + `calendar_backfill` (2026-09-25..29, `--data-root research-data/paper-shadow-operational/data`) on the operational DB; rollback snapshot `er1-autopilot/state/snapshots/operational-20260929T2100Z` | Verified sessions 2026-09-27..29; TODAY freshness moves from UNKNOWN to STALE truthfully |
| uid-999 scheduler/app containers (root Docker) | Container owner: expose the scheduler heartbeat/scan history to a readable path, or deploy a newer build | Scheduler heartbeat evidence, runtime at current HEAD |
| No dated authoritative EGX universe | Operator: a dated authoritative membership source | `authoritative_universe_available`, `overall_operational_ready` |
| No attested PRE-SURGE scorer | Research owner: an attested scorer-row producer | I |
| No US runtime universe/data | Operator: configured US universe and admitted US source | S |
