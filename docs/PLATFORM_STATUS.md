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
| B | Source admission | COMPLETE | EGX: the registry has typed access, entitlement, delay and evidence; identities are exact; it fails closed and gates SWING; TradingView EGX is admitted as operator-accepted with no licence. US: `app/us/sources.py` admits `tradingview_tvdatafeed_us` (operator mission 2026-10-03; no contractual licence), with Yahoo as report-only verification. The source stack and its alternatives are in `docs/SOURCE_DECISION_MATRIX.md`. |
| C | Canonical market data | COMPLETE | OHLC relationships, quarantine, duplicates, ordering, finite/positive checks; `adjusted_close` optional audit-only (`1d58103`); neutral row contract documented. |
| D | Security master | PARTIAL-BLOCKED | EGID identities with provenance, fail-closed refresh (empty/truncated). Dated authoritative membership needs an authoritative dated source (external). |
| E | Calendar and sessions | PARTIAL-BLOCKED | Verified-session freshness (no weekday arithmetic), historical session verification from admitted index evidence. Future holidays are not invented; forward verification needs admitted evidence. Operational DB sessions are verified through 2026-09-29 (official index evidence), so COMI (last bar 2026-09-24) is truthfully STALE. Since 2026-09-30 a daily egx-agent cron job (18:17 Cairo, pinned release) keeps it current, completed sessions only, fail-closed (`docs/EGX_CALENDAR_MAINTENANCE.md`); sessions 2026-09-15..29 are verified. Holidays remain UNKNOWN unless evidence exists. |
| F | TODAY | COMPLETE | Identities, daily observations with freshness, source status, readiness components and dimensions, receipts labelled by current admission. |
| G | SWING | COMPLETE | EGX-RANK-v1 per-symbol evidence (score, class, entry zone, stop, T1/T2, R:R, liquidity, momentum, trend, volume confirmation, rejection reasons, data warnings, session) plus system candidates and lifecycles on SWING (`docs/EGX_RANKING.md`). The reviewed manual SWING launch path is unchanged and separate. |
| H | LIVE | PARTIAL-BLOCKED | Shows the near-current feed as UNAVAILABLE plus declared source delay, latest market date and freshness; never real-time. Needs an admitted intraday/delayed feed. |
| I | PRE-SURGE | PARTIAL | Explainable volume-and-momentum expansion screen from the ranking report (not a prediction). The PreSurgeV7 attested-scorer contract is unchanged and still needs attested scorer rows. |
| J | Candidates and signals | COMPLETE | System-generated Paper/Shadow candidates (STRONG_CANDIDATE / CANDIDATE) are allowed by operator decision when hard gates pass (admitted source, session-current data, history, liquidity). Human review is optional. They are recorded in an append-only ledger. The legacy universe scan history is kept. |
| K | Paper/Shadow | COMPLETE | Deterministic lifecycle simulation from bars after the candidate session only: next-session entry rules, slippage and commission, stop-first same-bar rule, T1 half plus breakeven, T2 and 10-session time exit, MAE/MFE (`app/paper/system_candidates.py`). No orders, broker or live money. |
| L | Performance | PARTIAL | Computed only from closed simulated lifecycles (win rate, expectancy R, drawdown R, holding). EGX: the first candidates await entry. US: 75 candidates are PENDING_ENTRY for Monday 2026-10-05. Both are INSUFFICIENT_SAMPLE until 20 lifecycles close per market. This needs elapsed sessions, not code. |
| M | Research | PARTIAL | RESEARCH shows the context report (`app/context/`, `docs/PROVIDER_ARCHITECTURE.md`). Available: <br>• regimes (CONTEXT-v1); <br>• rates: Fed target, effective rate and 2y/10y, cross-checked FRED vs BIS; Egypt via the IMF, lagged and labelled; Fed releases; <br>• FX: USD/EGP ICE market rate vs ExchangeRate-API reference (reconciled), broad dollar index, EUR/USD; <br>• gold: OANDA vs TVC (reconciled); <br>• Brent futures vs EIA spot (the gap is reported); <br>• indices: EGX30, SPX, VIX; <br>• EGX official disclosures and financial statements; <br>• geopolitics: PortWatch chokepoints, OFAC SDN counts, GDELT narrative; <br>• cross-market correlations. <br>BLOCKED: <br>• the current CBE policy rate and official USD/EGP (cbe.org.eg rejects automated access); <br>• SEC filings and fundamentals (need an operator contact e-mail). <br>First live run 2026-10-03: every section AVAILABLE except SEC. |
| N | SYSTEM | COMPLETE | Build, checkpoint, heartbeat, scan history, receipts, source registry, runtime-state inputs, snapshot verification, readiness dimensions, coverage breakdown, and calendar-maintenance last outcome (CURRENT/STALE/FAILED/UNKNOWN; bundled with `--calendar-maintenance-status`). |
| O | Scheduler | PARTIAL-BLOCKED | egx-agent cron (pinned release `9cdd403`, Africa/Cairo). <br>• Sun–Thu 16:45: official market-watch capture. <br>• Sun–Thu 08:30: EGX chain (calendar → TradingView daily with EGX-XCHECK-v2 → ranking → universe scan → context → publish). <br>• Tue–Sat 06:15: US chain (`tools/us_nightly.sh`: US pipeline → context → publish). <br>Publication is serialised by `publish.lock`. The uid-999 `scheduler_worker` heartbeat is not visible (ADMIN_REQUIRED). |
| P | Read-only snapshot | COMPLETE | `tools/runtime_state_snapshot.py` (online backup, integrity, hashes, manifest v2, missing list, atomic, no overwrite) plus verification in SYSTEM. |
| Q | API | COMPLETE | `/api/product` and `/api/system` share readiness; end-to-end snapshot test; LIVE_MONEY label protected (`live` key). |
| R | UI | COMPLETE | All seven sections render truthful state from one shared theme. TODAY/SWING show the EGX and US candidate tables with a FUSION-v1 Context column (the class is never changed). RESEARCH has regime, rates, FX, gold, Brent, indices, news, geopolitics, fundamentals and cross-market panels (`docs/UI_INFORMATION_ARCHITECTURE.md`). |
| S | US market | PARTIAL | `app/us_run.py`: <br>• universe: Nasdaq Trader master plus the liquidity screen, top 500 by 30-day $ volume; current liquidity, forward research only; <br>• NYSE rule calendar, verified by S&P 500 bars; <br>• TradingView daily bars with identity checks (exchange, ISIN, NY/USD); <br>• immutable canonical artifacts; <br>• Yahoo cross-check sample; <br>• US-RANK-v1; <br>• Paper/Shadow ledger and lifecycles (US costs 5 bp slippage plus 5 bp commission per side). <br>First run 2026-10-03 for the Friday 2026-10-02 session: 500/500 acquired and CURRENT, session VERIFIED, cross-check 25/25 AGREE; 3 STRONG_CANDIDATE, 72 CANDIDATE, 81 WATCHLIST, 344 NO_TRADE; 75 lifecycles PENDING_ENTRY. <br>Remaining: dividends and corporate-action ledger (no free official feed); SEC fundamentals (contact). |
| T | Configuration | COMPLETE | Central resolver, bundle mode, MIXED_STATE_SOURCES warning; writers stay explicit-only. |
| U | Observability | COMPLETE | Structured, attributable records: raw manifests + `data_ingestions` (acquisition), `audit_events` (PIT validation, receipts), scan history JSON, heartbeat, snapshot manifest. Plus a path-free JSON `api_startup` event (build, runtime-state mode, input origins, snapshot status, warnings, LIVE_MONEY) logged once per process. |
| V | Security | COMPLETE | 2026-09-29 sweep: no shell/eval/pickle; SQL interpolation only of module constants; list-argument subprocesses; read-only GET routes, docs disabled; no tracked secrets. |
| W | Startup/deployment | COMPLETE | Pinned immutable releases and verified snapshot bundles. 8001 runs release `9cdd403` in pointer mode. The agent cuts over releases under the operator's standing gated authorisation, with spare-port validation and rollback (`docs/PREVIEW_RUNTIME.md`). nginx is unchanged. |
| X | Documentation | COMPLETE | This file; source decision matrix; provider architecture with data rights, runbooks and recovery; macro context; UI information architecture; runtime and snapshot; preview runtime; calendar maintenance; source evaluation and qualification; financial-services evaluation. |

## External blockers and owners

| Blocker | Owner / action | Unlocks |
|---|---|---|
| No admitted EGX per-equity daily source | Operator: supply the Twelve Data API key (`docs/TWELVE_DATA_PROVIDER.md` activation) and record the plan/terms review that admits XCAI end-of-day data for internal Paper/Shadow use. Market-watch rights stay a separate question | G, H (daily), I, J, K, L |
| 8001 public-preview cutover (approved, blocked for the agent) | Operator: run `docs/PREVIEW_RUNTIME.md` section 4 with release `1211939…` and snapshot `operational-20260930T0740Z` | Public preview served from a pinned release and a verified snapshot |
| uid-999 scheduler/app containers (root Docker) | Container owner: expose the scheduler heartbeat/scan history to a readable path, or deploy a newer build | Scheduler heartbeat evidence, runtime at current HEAD |
| No dated authoritative EGX universe | Operator: a dated authoritative membership source | `authoritative_universe_available`, `overall_operational_ready` |
| No attested PRE-SURGE scorer | Research owner: an attested scorer-row producer | I |
| No US runtime universe/data | Operator: configured US universe and admitted US source | S |
