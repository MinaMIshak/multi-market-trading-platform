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

## Reproducible read-only snapshot

Do not point the UI at a live SQLite writer. Take a snapshot instead:

```
python tools/runtime_state_snapshot.py \
  --db /abs/path/platform.db \
  --heartbeat /abs/path/scheduler-heartbeat.json \
  --scan-history /abs/path/egx-scan-history.json \
  --out /abs/path/snapshots/2026-09-29T1800Z
```

`--heartbeat` and `--scan-history` are optional; omit them if the files do not
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
- prints the environment for the UI process:
  `EGX_DB_PATH`, `EGX_PAPER_RUNTIME`, `EGX_SCAN_LEDGER_PATH` and, when copied,
  `EGX_SCHEDULER_HEARTBEAT_PATH` and `EGX_SCAN_HISTORY_PATH`.

A WAL source needs read access to its `-wal`/`-shm` files. If the snapshot
user cannot read them, SQLite refuses and the tool exits non-zero. Run it as
the database owner rather than weakening permissions.

A snapshot is historical state, not live state. The manifest's `taken_at` and
each evidence file's own timestamps say how old it is. Re-run the tool to
refresh, and switch the UI to the new directory. Absent inputs stay absent:
the tool never creates heartbeats, scan history or receipts.

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED.
