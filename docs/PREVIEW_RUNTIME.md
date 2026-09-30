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
  "env -i HOME=$HOME PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
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

## 4. Cut over port 8001 (operator-approved; the operator runs it)

With the 2026-09-30 release and snapshot, the full cutover command is:

```
tmux send-keys -t egx-ui-preview C-c
tmux new-session -d -s egx-preview-release \
  -c /home/egx-agent/er1-autopilot/state/releases/1211939330a2a77c2d8f6eb81dc08df452117624 \
  "env -i HOME=/home/egx-agent PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 EGX_SCAN_MODE=disabled \
   EGX_RUNTIME_STATE_DIR=/home/egx-agent/er1-autopilot/state/snapshots/operational-20260930T0740Z \
   EGX_BUILD_REVISION=1211939330a2a77c2d8f6eb81dc08df452117624 \
   /home/egx-agent/work/egx-trading-platform-us/.venv/bin/python -m uvicorn app.main:app \
   --host 127.0.0.1 --port 8001 --proxy-headers"
```

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
