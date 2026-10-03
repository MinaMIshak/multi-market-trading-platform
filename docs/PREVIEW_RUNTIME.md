# Public preview runtime: pinned release + verified snapshot

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED. This runbook serves the public preview
from an immutable source release and a read-only state snapshot, instead of a
live Git checkout with `--reload`.

## Host topology (observed 2026-09-29 on `egx-trading-platform` as `egx-agent`)

| Listener | Owner | What it is |
|---|---|---|
| 443/80 | root nginx | `3-126-217-243.sslip.io`, basic auth, `proxy_pass http://127.0.0.1:8001` |
| 127.0.0.1:8001 | egx-agent, tmux `egx-ui-preview` | public preview (see cutover below) |
| 127.0.0.1:8002 | egx-agent, tmux `egx-ui-snapshot` | isolated preview, `EGX_DB_PATH=er1-autopilot/state/runtime-preview/platform-preview.db` |
| 127.0.0.1:8765 | egx-agent | development uvicorn from the shared checkout |
| 127.0.0.1:8000, :18001 | uid 999, root Docker | separate app containers (not managed here) |
| — | uid 999, root Docker | `python -m app.core.scheduler_worker` (not managed here) |

Database ownership, from `/proc/<pid>/mountinfo` and file metadata:

- `/home/egx-agent/research-data/paper-shadow-operational/platform.db` is owned
  by `egx-agent` (WAL mode). The two uid-999 app containers mount that
  directory **read-only** at `/app/paper-runtime`. The uid-999 scheduler worker
  mounts no `/home/egx-agent` path, so it cannot write this file. No process
  held the file open when observed.
- The scheduler's own database and heartbeat live inside its container and
  are not visible to `egx-agent`. The snapshot therefore records the heartbeat
  and scan history as MISSING. It does not infer them.
- Opening a WAL database, even read-only, creates `platform.db-wal`/`-shm`
  companion files. These are normal SQLite reader files. Do not delete them
  while any process might have the database open.

Current public preview (cutover NOT yet performed): 8001 runs
`.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload`
from `/home/egx-agent/work/egx-trading-platform-us` with no `EGX_*` variables.
The uvicorn process is the tmux pane's own process, with no shell.
Every Git operation on that checkout reloads the public site. It reads the
non-existent default `/app/data/platform.db`, so every component shows
UNAVAILABLE.

## 1. Export a release

```
python tools/preview_release.py export --repo /abs/repo --commit <sha> \
    --out-root /home/egx-agent/er1-autopilot/state/releases
python tools/preview_release.py verify /home/egx-agent/er1-autopilot/state/releases/<full-sha>
```

The release holds `app/`, `requirements.txt` and `PROGRESS.json` at exactly that
commit, plus `RELEASE.json` (the SHA-256 of every file). It is read-only, is
never overwritten, and is independent of later checkout changes.

## 2. Take a state snapshot

```
python tools/runtime_state_snapshot.py \
    --db /home/egx-agent/research-data/paper-shadow-operational/platform.db \
    --out /home/egx-agent/er1-autopilot/state/snapshots/operational-<UTC stamp> \
    --build-revision <full-sha>
```

Add `--heartbeat`/`--scan-history` only when those files actually exist. See
`docs/RUNTIME_STATE_SNAPSHOT.md` for the consistency guarantees.

## 3. Start a candidate on a spare loopback port and validate it

```
tmux new-session -d -s egx-preview-candidate -c <release> \
  "env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
   EGX_SCAN_MODE=disabled EGX_RUNTIME_STATE_DIR=<snapshot> EGX_BUILD_REVISION=<full-sha> \
   /home/egx-agent/work/egx-trading-platform-us/.venv/bin/python -m uvicorn app.main:app \
   --host 127.0.0.1 --port 8011 --proxy-headers"
```

`env -i` gives the process only the variables it needs: no inherited
credentials and no stray `EGX_DB_PATH`. There is no `--reload`.

Acceptance checks:

- `GET /health` returns 200.
- `GET /api/system`: `live_money` false; `build.revision` is the release
  commit; `runtime_state.mode` is `SNAPSHOT_BUNDLE`; `snapshot.status` is
  `VERIFIED`; `warnings` is empty; `readiness.dimensions.overall_operational_ready`
  is false unless evidence supports otherwise.
- `GET /api/product`: `live` is `DISABLED`.
- `/?market=ALL&section=` each of TODAY, SWING, LIVE, PRE-SURGE, PERFORMANCE,
  RESEARCH and SYSTEM returns 200.

### Validation record (2026-09-29)

Release `99d0f4128b63d1aa44e480912f72f82f9e0b4dd9` (170 files, VERIFIED), with
snapshot `operational-20260929T2100Z` of the operational DB (integrity `ok`;
319 instruments, 2 daily artifacts, 139 sessions, 1 receipt; heartbeat and
scan history MISSING). The candidate ran on 127.0.0.1:8011:

- `/api/system`: build `99d0f41…`, mode `SNAPSHOT_BUNDLE`, snapshot `VERIFIED`,
  no warnings, `live_money` false.
- Readiness: `scan_readiness=EVIDENCE_BLOCKED`, `source_admission=NO_ADMITTED_SOURCE`,
  all downstream dimensions false.
- Daily freshness UNKNOWN: stored session evidence ends 2026-09-26.
- All seven sections returned 200.

### Validation record (2026-09-30)

Release `1211939330a2a77c2d8f6eb81dc08df452117624` (VERIFIED), with snapshot
`operational-20260930T0740Z` taken after the approved official-index and
calendar update (integrity `ok`; 142 sessions). All 18 acceptance checks passed
on 127.0.0.1:8011. Daily freshness is now `STALE` (2), because verified sessions
on 2026-09-27..29 follow COMI's 2026-09-24 bar. `scan_readiness` is still
`EVIDENCE_BLOCKED`, and `live_money` is false.

Cutover attempt: the old preview was stopped, but the agent's permission
policy blocked starting the release on 8001. The rollback below was run
immediately, restoring 8001 (health 200; public endpoint 401 without
credentials). The public preview was briefly unavailable. The approved
cutover has to be run by the operator.

### Validation record (2026-09-30, pre-cutover refresh)

The previous release and snapshot were superseded after the calendar
maintenance (sessions 142→149).

- Release `1369bcc1b4dc9924e67d7c882c2d382645a2f3e5` (171 files, VERIFIED).
- Snapshot `operational-20260930T0800Z`, taken 2026-09-30T08:00:37Z by online
  backup. Source and copy integrity are `ok`; 319 instruments, 149 sessions, 2
  daily artifacts, 1 receipt. Heartbeat and scan history are MISSING. All six
  lifecycle tables are at 0 and unchanged since the validated maintenance run.
- All 24 acceptance checks passed on 127.0.0.1:8011:
  - build and snapshot revision `1369bcc…`, exact snapshot path, `VERIFIED`,
    no runtime warnings;
  - `live_money` false and product `live` DISABLED;
  - 319 identities (312 equities);
  - `source_admission=NO_ADMITTED_SOURCE`, `scan_readiness=EVIDENCE_BLOCKED`,
    `overall_operational_ready` false, daily freshness `STALE` (2);
  - all seven sections return 200, and TODAY and SYSTEM show EVIDENCE_BLOCKED
    with no ADMITTED claim.
- 8001 was not touched. The operator then performed this cutover and
  verified it in a browser. 8001 has served release `1369bcc` with this
  snapshot since then.

### Current public release (2026-10-03, macro context; cut over by the agent)

Release `1bd75ae178eb8233c1466cf22a3435ae2b234cd3` (VERIFIED, 213 files) adds
the RESEARCH macro panel (`docs/MACRO_CONTEXT.md`) and the `macro`
runtime-state input. Sequence:
1. A first live fetch: 6 of 6 series AVAILABLE and CURRENT; 2s10s +46 bp.
2. Bundle `published-20261003T080647Z`, published with `--macro-report`. The
   then-live b418403 still verified it, because unknown files are only hashed.
3. All acceptance checks passed on 8011, then on 8001 after the cutover. Public
   HTTPS answers 401.
4. Crontab moved to this release; the 08:30 chain now runs step `macro` before
   `publish`.

Rollback: the adoption command with release `b418403055f3ebeae51d57c388b10a7dcdb80b6e`.

### Previous release (2026-10-03, polished dashboard; cut over by the agent)

Release `b418403055f3ebeae51d57c388b10a7dcdb80b6e` (exported, VERIFIED, 210
files) passed all acceptance checks on 127.0.0.1:8011 against the published
bundle `published-20261002T063119Z` (pointer mode). The agent then cut 8001
over to it at the operator's standing authorisation (2026-10-03, Saturday):
- post-cutover checks passed on 8001:
  - health 200 and build `b418403…`;
  - `SNAPSHOT_POINTER`, snapshot `VERIFIED`, warnings `[]`;
  - `live_money` false, and `overall_operational_ready` false;
  - the public HTTPS endpoint answers 401 without credentials, as before;
  - nginx unchanged;
- TODAY:
  - context grid, with freshness "220 of 221 current";
  - KPI cards: 221 / 221 / 220 / 221 / 20 / 1;
  - quick insights, with top idea CPCI (STRONG, score 82.18);
  - segmented filters: Actionable 47, All 221, Strong 1, Candidate 19,
    Watchlist 27, No trade 174;
  - search, and per-row collapsed evidence;
- freshness fix: on weekends the readers showed 0 session-current, because
  Friday has no stored session row. Missing Friday/Saturday rows now count as
  non-trading by the operator's fixed weekend rule. A missing Sunday–Thursday
  row is still never inferred;
- crontab: both entries (16:45 capture, 08:30 chain, Sun–Thu) moved to this
  release.

Rollback: the step-5 adoption command with release
`34dd2f536c3bd90ef1dc1a7046cf9c47b1db6c1e` (VERIFIED), and the crontab paths
moved back the same way.

### Previous release (2026-10-02, research-first UI; applied by the operator, now the rollback)

8001 already runs in pointer mode (on release `e448206`) and is already
serving the 2026-10-02 published bundle. Rendering the ranking, candidates,
licensing labels and coverage needs the new code, which takes one restart.
Release `34dd2f536c3bd90ef1dc1a7046cf9c47b1db6c1e` passed all acceptance
checks on 127.0.0.1:8011 against that same published bundle (pointer mode):
- research-first layout (`docs/UI_INFORMATION_ARCHITECTURE.md`): TODAY opens
  with session context, summary cards (221 scanned, 221 admitted, 220
  current, 221 ranked, 20 candidates, 1 strong) and the sortable candidate
  table (47 rows, no UNKNOWN cells). All 222 receipt verification-window lines
  are collapsed in diagnostics;
- TradingView admitted with the label `NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED`
  (the only admitted source);
- the ranking report is present, and TODAY/SWING show it with the licensing
  label and the note "Based on the Thursday 2026-10-01 close; prepared ...
  ahead of the next expected EGX session, Sunday 2026-10-04";
- coverage: observed 221, admitted 221, session-current 220, ranked 221,
  candidates 20;
- `overall_operational_ready` is false, and `live_money` is false.

```
tmux kill-session -t egx-preview-release
while ss -ltn | grep -q '127.0.0.1:8001 '; do sleep 0.5; done
tmux new-session -d -s egx-preview-release \
  -c /home/egx-agent/er1-autopilot/state/releases/34dd2f536c3bd90ef1dc1a7046cf9c47b1db6c1e \
  "env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 EGX_SCAN_MODE=disabled \
   EGX_RUNTIME_STATE_POINTER=/home/egx-agent/er1-autopilot/state/public-bundle.json \
   EGX_BUILD_REVISION=34dd2f536c3bd90ef1dc1a7046cf9c47b1db6c1e \
   /home/egx-agent/work/egx-trading-platform-us/.venv/bin/python -m uvicorn app.main:app \
   --host 127.0.0.1 --port 8001 --proxy-headers"
```

Rollback: the same commands with release `e4482062c896747fda65b4aa50faaf253edf0fe8`.

### Earlier validated release (2026-09-30 evening)

- Release `e146fab74b2194d78914ee90b0458fdd20175d82` (175 files, VERIFIED): the
  coverage breakdown, the plain candidate header, SYSTEM calendar-maintenance
  status, and the market-watch adapter (blocked, not scheduled).
- Snapshot `operational-20260930T1520Z` (taken 15:20:59Z): integrity `ok`, 149
  sessions, calendar-maintenance record bundled; heartbeat and scan history
  MISSING.
- All acceptance checks passed on 127.0.0.1:8011. Coverage: identities 319,
  equities 312, universe UNKNOWN, observed 1, admitted 0, current 0,
  scanned/candidates UNKNOWN; US all UNKNOWN. Calendar maintenance: SUCCESS /
  CURRENT / `DEFERRED_SNAPSHOT_DATE_ALREADY_USED`.

Apply it with the "Update 8001 from the running release" commands below, using
this release and snapshot. Expected check output:
`False e146fab74b2194d78914ee90b0458fdd20175d82 VERIFIED [] 319`.

### Release update record (2026-09-30, coverage breakdown; superseded by the latest release above)

- Release `39e9966b2429784dce4e92f64b9250cb963153b6` (172 files, VERIFIED).
  It adds the coverage breakdown and the plain "A candidate is not a fill"
  header.
- Snapshot `operational-20260930T0836Z`: integrity `ok`, 319 instruments, 149
  sessions, heartbeat and scan history MISSING.
- All acceptance checks passed on 127.0.0.1:8011. TODAY and `/api/system`
  report the same breakdown: identities 319, equities 312, authoritative
  universe UNKNOWN, observed 1, admitted 0, current 0, admitted and current 0,
  scanned UNKNOWN, candidates UNKNOWN. US is all UNKNOWN, and the page
  contains no `!=`.

Update 8001 from the running release (the operator runs this):

```
tmux kill-session -t egx-preview-release
while ss -ltn | grep -q '127.0.0.1:8001 '; do sleep 0.5; done
tmux new-session -d -s egx-preview-release \
  -c /home/egx-agent/er1-autopilot/state/releases/39e9966b2429784dce4e92f64b9250cb963153b6 \
  "env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 EGX_SCAN_MODE=disabled \
   EGX_RUNTIME_STATE_DIR=/home/egx-agent/er1-autopilot/state/snapshots/operational-20260930T0836Z \
   EGX_BUILD_REVISION=39e9966b2429784dce4e92f64b9250cb963153b6 \
   /home/egx-agent/work/egx-trading-platform-us/.venv/bin/python -m uvicorn app.main:app \
   --host 127.0.0.1 --port 8001 --proxy-headers 2>&1 | tee -a /home/egx-agent/er1-autopilot/state/preview-8001.log"
until curl -sf -o /dev/null http://127.0.0.1:8001/health; do sleep 0.5; done
curl -s http://127.0.0.1:8001/api/system | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['live_money'],d['build']['revision'],d['runtime_state']['snapshot']['status'],d['runtime_state']['warnings'],{r['level']:r['count'] for r in d['coverage_breakdown']['EGX']}['identities'])"
```

Expected output: `False 39e9966b2429784dce4e92f64b9250cb963153b6 VERIFIED [] 319`.
Otherwise, go back to the previous pair: run the same commands with release
`1369bcc1b4dc9924e67d7c882c2d382645a2f3e5`, snapshot
`operational-20260930T0800Z`, and drop the final `coverage_breakdown` field
from the check.

## 4. Cut over port 8001 (operator-approved; the operator runs it)

Current validated pair: release `1369bcc1b4dc9924e67d7c882c2d382645a2f3e5`,
snapshot `operational-20260930T0800Z`. Cutover:

```
tmux send-keys -t egx-ui-preview C-c
while ss -ltn | grep -q '127.0.0.1:8001 '; do sleep 0.5; done
tmux new-session -d -s egx-preview-release \
  -c /home/egx-agent/er1-autopilot/state/releases/1369bcc1b4dc9924e67d7c882c2d382645a2f3e5 \
  "env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 EGX_SCAN_MODE=disabled \
   EGX_RUNTIME_STATE_DIR=/home/egx-agent/er1-autopilot/state/snapshots/operational-20260930T0800Z \
   EGX_BUILD_REVISION=1369bcc1b4dc9924e67d7c882c2d382645a2f3e5 \
   /home/egx-agent/work/egx-trading-platform-us/.venv/bin/python -m uvicorn app.main:app \
   --host 127.0.0.1 --port 8001 --proxy-headers 2>&1 | tee -a /home/egx-agent/er1-autopilot/state/preview-8001.log"
until curl -sf -o /dev/null http://127.0.0.1:8001/health; do sleep 0.5; done
curl -s http://127.0.0.1:8001/api/system | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['live_money'],d['build']['revision'],d['runtime_state']['snapshot']['status'],d['runtime_state']['warnings'])"
curl -s -o /dev/null -w '%{http_code}\n' https://3-126-217-243.sslip.io/
```

Expected output: `False 1369bcc1b4dc9924e67d7c882c2d382645a2f3e5 VERIFIED []`,
then `401`. On any other output, run the rollback.

General form:

nginx is root-owned and stays unchanged. The cutover replaces the process
behind 8001, which `egx-agent` owns:

```
tmux send-keys -t egx-ui-preview C-c          # stop the --reload preview (its pane has no shell, so the session ends)
tmux new-session -d -s egx-preview-release -c <release> "<the step 3 command with --port 8001>"
curl -s http://127.0.0.1:8001/api/system       # repeat the step 3 checks
curl -s -o /dev/null -w '%{http_code}\n' https://3-126-217-243.sslip.io/   # expect 401 without credentials
```

## Rollback

```
tmux kill-session -t egx-preview-release
tmux new-session -d -s egx-ui-preview -c /home/egx-agent/work/egx-trading-platform-us \
  '.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload'
```

This rollback was exercised on 2026-09-30 and restored 8001 within seconds.
To go back to an earlier release or snapshot instead, start step 4 with that
release or snapshot directory. Releases and snapshots are never modified.

## Refresh

To publish new code or state, export a new release and/or take a new
snapshot, validate it on a spare port (step 3), then repeat step 4.

## 5. Pointer mode: nightly state publication without restarts

A runtime started with `EGX_RUNTIME_STATE_POINTER=<file>` (and without
`EGX_RUNTIME_STATE_DIR`) serves the bundle named in that JSON file. It re-reads
the file whenever it changes. `tools/publish_runtime_snapshot.py publish`:
1. takes a new snapshot;
2. re-verifies it the way the runtime does;
3. switches the pointer atomically, only when the snapshot is VERIFIED and holds
   instruments.

On any failure the pointer is left unchanged and the command exits 1. Every
switch is appended to `<pointer>.history.jsonl`. SYSTEM shows mode
`SNAPSHOT_POINTER`, the pointer status, the publish time and the build.
Warnings: `RUNTIME_STATE_POINTER_UNREADABLE`, `BUNDLE_AND_POINTER_BOTH_SET`.
Code releases still go through steps 1–4. Only state changes are published
this way.

Paths:
- Pointer: `/home/egx-agent/er1-autopilot/state/public-bundle.json`
- History: `/home/egx-agent/er1-autopilot/state/public-bundle.json.history.jsonl`
- Published bundles: `/home/egx-agent/er1-autopilot/state/snapshots/published-<UTC>`

Nightly publication (egx-agent crontab, 19:05 Cairo, after the calendar job
at 18:17 and the universe scan at 18:45; marker `# EGX_PUBLISH_SNAPSHOT`):

```
5 19 * * * cd /home/egx-agent/er1-autopilot/state/releases/<sha> && env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 TZ=Africa/Cairo /home/egx-agent/work/egx-trading-platform-us/.venv/bin/python tools/publish_runtime_snapshot.py publish --db /home/egx-agent/research-data/paper-shadow-operational/platform.db --snapshots-root /home/egx-agent/er1-autopilot/state/snapshots --pointer /home/egx-agent/er1-autopilot/state/public-bundle.json --build-revision <sha> --scan-history /home/egx-agent/er1-autopilot/state/egx-scan/egx-scan-history.json --calendar-maintenance-status /home/egx-agent/er1-autopilot/state/egx-calendar-maintenance/last-run.json >> /home/egx-agent/er1-autopilot/state/publish-snapshot.log 2>&1 # EGX_PUBLISH_SNAPSHOT
```

Until 8001 is restarted in pointer mode, the cron only maintains the pointer
and the public site is unaffected. One-time adoption (operator, same shape as
step 4). Swap `EGX_RUNTIME_STATE_DIR=<snapshot>` for
`EGX_RUNTIME_STATE_POINTER=/home/egx-agent/er1-autopilot/state/public-bundle.json`:

```
tmux kill-session -t egx-preview-release
while ss -ltn | grep -q '127.0.0.1:8001 '; do sleep 0.5; done
tmux new-session -d -s egx-preview-release -c /home/egx-agent/er1-autopilot/state/releases/<sha> \
  "env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 EGX_SCAN_MODE=disabled \
   EGX_RUNTIME_STATE_POINTER=/home/egx-agent/er1-autopilot/state/public-bundle.json \
   EGX_BUILD_REVISION=<sha> /home/egx-agent/work/egx-trading-platform-us/.venv/bin/python -m uvicorn app.main:app \
   --host 127.0.0.1 --port 8001 --proxy-headers"
```

Check: `/api/system` → `runtime_state.mode` `SNAPSHOT_POINTER`,
`snapshot.status` `VERIFIED`, `warnings` `[]`.

Rollback of state: `tools/publish_runtime_snapshot.py rollback --pointer <pointer>`.
Rollback of the mode: restart 8001 with `EGX_RUNTIME_STATE_DIR=<snapshot>` as
in step 4. Disable publication:
`crontab -l | grep -v '# EGX_PUBLISH_SNAPSHOT$' | crontab -`.
