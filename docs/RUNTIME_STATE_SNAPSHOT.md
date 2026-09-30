# Runtime readiness semantics and read-only state snapshots

## Why an isolated runtime showed EGX `configured=false / NOT_READY`

The product API combines readers that are configured independently. A runtime
that sets only `EGX_DB_PATH` gets identities and daily observations, but none
of the operational evidence:

| Product field | Reader | Configuration | Carried by a DB copy? |
|---|---|---|---|
| Security master, daily observations | `app.ui.today` | `EGX_DB_PATH` | yes |
| `markets.EGX.configured/available/status` (receipts) | `app.ui.operational.load_operational_state` | `EGX_PAPER_RUNTIME` = directory containing `platform.db` | yes, if that directory is set |
| Scheduler heartbeat | `app.scheduler_heartbeat.load_heartbeat` | `EGX_SCHEDULER_HEARTBEAT_PATH` (JSON file) | **no**: separate file |
| Scan history | `app.egx_scan_history.load_scan_history` | `EGX_SCAN_HISTORY_PATH` (JSON file) | **no**: separate file |
| Scan completion reconciliation | `reconcile_scan_completion` | `EGX_SCAN_LEDGER_PATH` (SQLite `scheduled_jobs`) | yes, if set |

Evidence can also be absent at the source. The scheduler defaults to
`EGX_SCHEDULER_MODE=observe` and `EGX_SCAN_MODE=disabled`, which means:
- No scan history is written unless scanning is enabled.
- A heartbeat is written only when `EGX_SCHEDULER_HEARTBEAT_PATH` is set for the worker.
- `PAPER_SIGNAL_VERIFIED` receipts exist only where a SWING verification actually ran.

Since `dabc8db`, SWING verification is `EVIDENCE_BLOCKED` for every source that
is not ADMITTED. No EGX daily source is admitted, so no new receipts are
produced. Earlier receipts, if present, remain visible and labelled.

## Readiness is per component

`/api/product` now includes `readiness.EGX`, built by `app/ui/readiness.py`. It
is also rendered in TODAY/SWING/SYSTEM, and `/api/system` exposes it as
`readiness`. It reports these components separately:

- `canonical_data`: AVAILABLE / PARTIAL / UNAVAILABLE (identities + daily bars)
- `security_master`, `daily_observations` (counts, freshness counts)
- `source_admission`: from the source registry only; row claims are ignored
- `scheduler_heartbeat`, `scan_history`, `operational_receipts`
- `scan_readiness`: `EVIDENCE_BLOCKED` when no admitted daily source,
  otherwise `NOT_READY` while any blocker remains, otherwise
  `PREREQUISITES_MET`. It is never `READY`, and meeting the prerequisites is
  not a candidate or signal.
- `blockers`: explicit codes such as `NO_ADMITTED_DAILY_SOURCE`,
  `SCHEDULER_HEARTBEAT_UNAVAILABLE`, `SCAN_HISTORY_UNAVAILABLE`,
  `OPERATIONAL_RECEIPTS_NOT_CONFIGURED`.

The receipt-reader fields (`markets.EGX.status`) and coverage counts are
unchanged. Identities or bars never upgrade them.

## Central runtime-state resolution

`app/runtime_state.py` is the single resolver used by every reader. Each
input resolves in this order:

1. its explicit variable (`EGX_DB_PATH`, `EGX_PAPER_RUNTIME`,
   `EGX_SCHEDULER_HEARTBEAT_PATH`, `EGX_SCAN_HISTORY_PATH`,
   `EGX_SCAN_LEDGER_PATH`), kept for compatibility;
2. `EGX_RUNTIME_STATE_DIR`, a snapshot bundle directory;
3. the legacy default (only the product DB has one: `/app/data/platform.db`).

Writers (the scheduler's heartbeat and scan history) still use only their
explicit variables, so nothing ever writes into a bundle.

`/api/system` → `runtime_state` reports each input's variable, origin and path.
For a bundle it also reports a manifest check: every hash is re-verified, with
the database integrity result, build revision, missing artifacts and
mismatches. Status is `VERIFIED` only when the database hash matches and
`integrity_check` is `ok`; otherwise it is `INVALID` or `UNAVAILABLE`.
It raises these warnings:
- `MIXED_STATE_SOURCES` when explicit overrides split state across sources,
  whether combined with a bundle or with each other;
- `SNAPSHOT_UNVERIFIED`;
- `RUNTIME_STATE_DIR_NOT_ABSOLUTE`.

The tests include an end-to-end check (`tests/test_runtime_state.py`):
snapshot → bundle → `/api/product` + `/api/system` → identical, truthful
readiness, including tamper detection.

## Reproducible read-only snapshot

Do not point the UI at a live SQLite writer. Take a snapshot instead:

```
python tools/runtime_state_snapshot.py \
  --db /abs/path/platform.db \
  --heartbeat /abs/path/scheduler-heartbeat.json \
  --scan-history /abs/path/egx-scan-history.json \
  --out /abs/path/snapshots/2026-09-29T1800Z \
  --build-revision "$(git rev-parse HEAD)"

EGX_RUNTIME_STATE_DIR=/abs/path/snapshots/2026-09-29T1800Z \
EGX_BUILD_REVISION="$(git rev-parse HEAD)" \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

To refresh, take a new snapshot into a new directory, then restart the UI with
the new path. To roll back, restart with the previous directory; old bundles
are never modified.

`--heartbeat`, `--scan-history` and `--calendar-maintenance-status` (the calendar
job's `last-run.json`, shown in SYSTEM) are optional; omit them if the files do not
exist. The tool:

- opens the source with `mode=ro` and `PRAGMA query_only=ON`, and never uses
  `immutable=1` (unsafe with a live writer);
- copies with SQLite's online backup API in one step. That is one consistent
  read transaction: committed rows only. In WAL mode it does not block the
  writer. In rollback-journal mode it holds a brief shared lock, so run it when
  the writer is idle if its busy timeout is short;
- runs `PRAGMA integrity_check` on the copy and fails closed unless it is `ok`;
- copies the heartbeat and history JSON byte-for-byte with their original
  timestamps, so an old heartbeat reads STALE;
- writes `SNAPSHOT_MANIFEST.json` with source paths, SHA-256 hashes, integrity
  result, key table counts and the `PAPER_SIGNAL_VERIFIED` receipt count;
- builds a private partial directory, sets the files read-only (0444) and
  renames it atomically. It never overwrites, and a failed attempt leaves nothing;
- records `--build-revision` (default `EGX_BUILD_REVISION`) and lists missing
  artifacts in the manifest (`schema_version` 2);
- prints the one variable the UI process needs: `EGX_RUNTIME_STATE_DIR=<out>`.

A WAL source needs read access to its `-wal`/`-shm` files. If the snapshot
user cannot read them, SQLite refuses and the tool exits non-zero. Run it as
the database owner rather than weakening permissions.

A snapshot is historical state, not live state. The manifest's `taken_at` and
each evidence file's own timestamps say how old it is. Re-run the tool to
refresh, and switch the UI to the new directory. Absent inputs stay absent:
the tool never creates heartbeats, scan history or receipts.

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED.
