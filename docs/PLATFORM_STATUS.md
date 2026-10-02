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
- 2026-09-30 (approved): official index bars 2026-09-27..29 were admitted to the
  operational DB (`source_snapshot_date=2026-09-30`), and a calendar backfill
  verified 2026-09-27..29 (`HISTORICAL_OFFICIAL`). Integrity was `ok` before and
  after. Only `canonical_data_artifacts`/`canonical_artifact_sources` (6→9),
  `data_ingestions` (12→15) and `market_sessions` (139→142) changed; every
  lifecycle table stayed at 0. Backup: `er1-autopilot/state/snapshots/pre-calendar-write-20260930T0000Z`
  and `pre-calendar-write-data-20260930.tgz`. A first run pinned the wrong
  snapshot date (2026-09-29). It was rolled back from that backup before the
  backfill, and its files were quarantined in `er1-autopilot/state/quarantine/`.
- The pinned release `1211939` plus snapshot `operational-20260930T0740Z` passes
  every acceptance check on 127.0.0.1:8011 (daily freshness now STALE). The 8001
  cutover was attempted and blocked by the agent permission policy. 8001 was
  restored to its previous `--reload` process within seconds. The cutover
  awaits the operator (see `docs/PREVIEW_RUNTIME.md` section 4).

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
| A | Market data acquisition | COMPLETE | Zero-cost strategy (operator decision 2026-10-01, `docs/OPERATOR_DECISIONS.md`): TradingView via the tvdatafeed protocol is the primary EGX daily source (`docs/TRADINGVIEW_PROVIDER.md`), with discovery, exact ISIN mapping (299 matched), raw provenance, Cairo session dating, the reviewed validation/quarantine pipeline, resolved-ISIN identity checks and an official market-watch cross-check with quarantine. Backfill 2026-10-01: 219 stored, 80 rejected fail-closed (33 empty, 25 stale, 18 non-EGP/stock, 4 short history). Official market-watch: verification only. Twelve Data: optional (paid for EGX). |
| B | Source admission | COMPLETE | Registry with typed access/entitlement/delay/evidence; exact-identity, fail-closed; gates SWING; shown in TODAY/SWING/LIVE/SYSTEM. No source is admitted (truthful). |
| C | Canonical market data | COMPLETE | OHLC relationships, quarantine, duplicates, ordering, finite/positive checks; `adjusted_close` optional audit-only (`1d58103`); neutral row contract documented. |
| D | Security master | PARTIAL-BLOCKED | EGID identities with provenance, fail-closed refresh (empty/truncated). Dated authoritative membership needs an authoritative dated source (external). |
| E | Calendar and sessions | PARTIAL-BLOCKED | Verified-session freshness (no weekday arithmetic), historical session verification from admitted index evidence. Future holidays are not invented; forward verification needs admitted evidence. Operational DB sessions are verified through 2026-09-29 (official index evidence), so COMI (last bar 2026-09-24) is truthfully STALE. Since 2026-09-30 a daily egx-agent cron job (18:17 Cairo, pinned release) keeps it current, completed sessions only, fail-closed (`docs/EGX_CALENDAR_MAINTENANCE.md`); sessions 2026-09-15..29 are verified. Holidays remain UNKNOWN unless evidence exists. |
| F | TODAY | COMPLETE | Identities, daily observations with freshness, source status, readiness components and dimensions, receipts labelled by current admission. |
| G | SWING | COMPLETE | EGX-RANK-v1 per-symbol evidence (score, class, entry zone, stop, T1/T2, R:R, liquidity, momentum, trend, volume confirmation, rejection reasons, data warnings, session) plus system candidates and lifecycles on SWING (`docs/EGX_RANKING.md`). The reviewed manual SWING launch path is unchanged and separate. |
| H | LIVE | PARTIAL-BLOCKED | Shows the near-current feed as UNAVAILABLE plus declared source delay, latest market date and freshness; never real-time. Needs an admitted intraday/delayed feed. |
| I | PRE-SURGE | PARTIAL | Explainable volume-and-momentum expansion screen from the ranking report (not a prediction). The PreSurgeV7 attested-scorer contract is unchanged and still needs attested scorer rows. |
| J | Candidates and signals | COMPLETE | System-generated Paper/Shadow candidates (STRONG_CANDIDATE / CANDIDATE) are allowed by operator decision when hard gates pass (admitted source, session-current data, history, liquidity). Human review is optional. They are recorded in an append-only ledger. The legacy universe scan history is kept. |
| K | Paper/Shadow | COMPLETE | Deterministic lifecycle simulation from bars after the candidate session only: next-session entry rules, slippage and commission, stop-first same-bar rule, T1 half plus breakeven, T2 and 10-session time exit, MAE/MFE (`app/paper/system_candidates.py`). No orders, broker or live money. |
| L | Performance | PARTIAL | Computed only from closed simulated lifecycles (win rate, expectancy R, drawdown R, holding). The first 20 system candidates (Thursday 2026-10-01 close) await their next-session entries, so status is INSUFFICIENT_SAMPLE until 20 lifecycles close. It needs elapsed sessions, not code. |
| M | Research | COMPLETE | `ResearchNote` contract (SOURCE_FACT / DERIVED_METRIC / MODEL_INTERPRETATION / UNKNOWN); receipt-derived notes; financial-services recorded and re-checked. |
| N | SYSTEM | COMPLETE | Build, checkpoint, heartbeat, scan history, receipts, source registry, runtime-state inputs, snapshot verification, readiness dimensions, coverage breakdown, and calendar-maintenance last outcome (CURRENT/STALE/FAILED/UNKNOWN; bundled with `--calendar-maintenance-status`). |
| O | Scheduler | PARTIAL-BLOCKED | egx-agent cron (pinned release `f618abb`), Sun-Thu Africa/Cairo: 16:45 official market-watch post-close capture; 08:30 pre-market chain (calendar → TradingView daily with EGX-XCHECK-v2 cross-check → ranking and system candidates → universe scan → verified publication). Run 2026-10-02 (processing Thursday 2026-10-01): every step exit 0. The uid-999 `scheduler_worker` heartbeat is not visible (ADMIN_REQUIRED). |
| P | Read-only snapshot | COMPLETE | `tools/runtime_state_snapshot.py` (online backup, integrity, hashes, manifest v2, missing list, atomic, no overwrite) plus verification in SYSTEM. |
| Q | API | COMPLETE | `/api/product` and `/api/system` share readiness; end-to-end snapshot test; LIVE_MONEY label protected (`live` key). |
| R | UI | COMPLETE | All seven sections render truthful state. TODAY/SWING/SYSTEM show a coverage breakdown: identities 319, equities 312, universe UNKNOWN, observed/admitted/current/scanned/candidates each on their own evidence. The header reads "A candidate is not a fill". |
| S | US market | PARTIAL-BLOCKED | US0–US8 historical contracts in `app/us`, with tests. There is no configured US runtime universe or admitted US data source, so the product shows US as UNKNOWN. |
| T | Configuration | COMPLETE | Central resolver, bundle mode, MIXED_STATE_SOURCES warning; writers stay explicit-only. |
| U | Observability | COMPLETE | Structured, attributable records: raw manifests + `data_ingestions` (acquisition), `audit_events` (PIT validation, receipts), scan history JSON, heartbeat, snapshot manifest. Plus a path-free JSON `api_startup` event (build, runtime-state mode, input origins, snapshot status, warnings, LIVE_MONEY) logged once per process. |
| V | Security | COMPLETE | 2026-09-29 sweep: no shell/eval/pickle; SQL interpolation only of module constants; list-argument subprocesses; read-only GET routes, docs disabled; no tracked secrets. |
| W | Startup/deployment | PARTIAL-BLOCKED | Pinned releases plus verified snapshots. 8001 runs in pointer mode (operator-adopted) and serves each published bundle without a restart. Rendering the ranking needs the one-time operator restart onto `f618abb` (`docs/PREVIEW_RUNTIME.md`). nginx and the uid-999 containers are root-owned (ADMIN_REQUIRED). |
| X | Documentation | COMPLETE | This file; runtime/snapshot, preview runtime, calendar maintenance, source evaluation and market-watch qualification, source qualification, financial-services evaluation. |

## External blockers and owners

| Blocker | Owner / action | Unlocks |
|---|---|---|
| No admitted EGX per-equity daily source | Operator: supply the Twelve Data API key (`docs/TWELVE_DATA_PROVIDER.md` activation) and record the plan/terms review that admits XCAI end-of-day data for internal Paper/Shadow use. Market-watch rights stay a separate question | G, H (daily), I, J, K, L |
| 8001 public-preview cutover (approved, blocked for the agent) | Operator: run `docs/PREVIEW_RUNTIME.md` section 4 with release `1211939…` and snapshot `operational-20260930T0740Z` | Public preview served from a pinned release and a verified snapshot |
| uid-999 scheduler/app containers (root Docker) | Container owner: expose the scheduler heartbeat/scan history to a readable path, or deploy a newer build | Scheduler heartbeat evidence, runtime at current HEAD |
| No dated authoritative EGX universe | Operator: a dated authoritative membership source | `authoritative_universe_available`, `overall_operational_ready` |
| No attested PRE-SURGE scorer | Research owner: an attested scorer-row producer | I |
| No US runtime universe/data | Operator: configured US universe and admitted US source | S |
