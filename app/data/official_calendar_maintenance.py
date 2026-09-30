"""Daily maintenance of official EGX index evidence and calendar truth.

Wraps the reviewed ``egx_official_public`` refresh job and the offline
calendar backfill for unattended (cron) use:

- Only completed sessions: the last eligible market date is today (Africa/Cairo)
  only after ``SESSION_COMPLETE_AT``, otherwise yesterday. Today's open
  session is never fetched or backfilled.
- Idempotent: fetching starts the day after the newest official bar already
  admitted for *every* calendar index, so a repeat run re-admits nothing. The
  backfill over a trailing window is deterministic.
- Fail closed: every index is probed before anything is written. Any
  provider/network error, an index missing bars, or a disagreement between
  indices writes nothing (exit 1). No bars in range is a clean no-op
  (holidays and weekends stay whatever the evidence supports, usually UNKNOWN).
  If the reviewed refresh job itself fails, its immutable raw ingestion may
  remain as RECEIVED (never promoted, never read as data).
- Official index artifacts are immutable per (index, snapshot date). If today's
  snapshot date is already used, the fetch is deferred to the next run
  (``DEFERRED_SNAPSHOT_DATE_ALREADY_USED``) and never collides.
- Provenance: ``snapshot_date`` is the Cairo date at acquisition, never pinned.
- Guarded: DB integrity must be ``ok`` before and after, and lifecycle tables
  (candidates, signals, positions, plans, outcomes, risk decisions) must not
  change. A non-blocking lock prevents overlapping runs (exit 75).
- Observable: one JSON record per run is appended to a log, and the last
  outcome is written atomically to a status file. No secrets are logged.

It never touches LIVE_MONEY or any execution path and does not infer
exchange membership from index evidence.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

CAIRO = ZoneInfo("Africa/Cairo")
# EGX continuous trading ends 14:30 Cairo; official index closes are
# published after that. Earlier runs treat today as not yet complete.
SESSION_COMPLETE_AT = time(16, 0)
BACKFILL_WINDOW_DAYS = 14
PROVIDER = "egx_official_public"
LIFECYCLE_TABLES = ("candidates", "signals", "positions", "trade_plans",
                    "trade_outcomes", "risk_decisions")
EVIDENCE_TABLES = ("canonical_data_artifacts", "canonical_artifact_sources",
                   "data_ingestions", "market_sessions")
EXIT_OK, EXIT_FAILED, EXIT_LOCKED = 0, 1, 75


class MaintenanceError(RuntimeError):
    """A fail-closed stop; ``code`` is a stable, secret-free reason."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def last_completed_session_date(now: datetime) -> date:
    local = now.astimezone(CAIRO)
    if local.time() >= SESSION_COMPLETE_AT:
        return local.date()
    return local.date() - timedelta(days=1)


def _read_only(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def inspect_database(db_path: Path) -> dict:
    with closing(_read_only(db_path)) as con:
        counts = {}
        for table in (*LIFECYCLE_TABLES, *EVIDENCE_TABLES):
            try:
                counts[table] = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.OperationalError:
                counts[table] = None
        return {"integrity": con.execute("PRAGMA integrity_check").fetchone()[0],
                "counts": counts}


def newest_official_bar_date(db_path: Path, index_names) -> date | None:
    """Oldest of the per-index newest admitted bars; None if any index lacks one."""
    with closing(_read_only(db_path)) as con:
        newest = dict(con.execute(
            "SELECT symbol, MAX(newest_market_date) FROM canonical_data_artifacts "
            "WHERE provider=? AND asset_type='INDEX_BARS' AND status='VALIDATED' "
            "GROUP BY symbol", (PROVIDER,)).fetchall())
    if any(not newest.get(name) for name in index_names):
        return None
    return min(date.fromisoformat(newest[name]) for name in index_names)


def snapshot_date_used(db_path: Path, index_names, snapshot_date: date) -> bool:
    """True when any calendar index already has an official artifact for this snapshot date."""
    marks = ",".join("?" for _ in index_names)
    with closing(_read_only(db_path)) as con:
        row = con.execute(
            "SELECT COUNT(*) FROM canonical_data_artifacts WHERE provider=? "
            f"AND asset_type='INDEX_BARS' AND source_snapshot_date=? AND symbol IN ({marks})",
            (PROVIDER, snapshot_date.isoformat(), *index_names)).fetchone()
    return row[0] > 0


def probe(provider, index_names, start: date, end: date) -> int:
    """Records per index over [start, end]; all indices must agree."""
    counts = {}
    for name in index_names:
        try:
            counts[name] = provider.fetch_index_bars(
                index_name=name, start_date=start, end_date=end).record_count
        except Exception as exc:  # network, HTTP, validation: fail closed
            raise MaintenanceError(f"PROBE_FAILED:{name}:{type(exc).__name__}") from exc
    if len(set(counts.values())) != 1:
        raise MaintenanceError("INDEX_EVIDENCE_DISAGREES:" + ",".join(
            f"{name}={count}" for name, count in sorted(counts.items())))
    return next(iter(counts.values()))


def _write_status(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(record, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _append_log(directory: Path, record: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = record["started_at"][:7].replace("-", "")
    with (directory / f"egx-calendar-maintenance-{stamp}.jsonl").open("a") as log:
        log.write(json.dumps(record, sort_keys=True) + "\n")


def run(*, db_path: Path, data_root: Path, now: datetime, provider=None,
        refresh_runtime_factory=None, backfill_runtime_factory=None) -> dict:
    """One maintenance pass; returns a result record or raises MaintenanceError."""
    from app.core.calendar_backfill import build_calendar_backfill_runtime
    from app.data.official_index_refresh_runtime import build_official_index_refresh_runtime
    from app.data.providers.egx_official import EGXOfficialPublicProvider
    from app.storage.database import Database

    if not db_path.is_file():
        raise MaintenanceError("DATABASE_MISSING")
    before = inspect_database(db_path)
    if before["integrity"] != "ok":
        raise MaintenanceError("INTEGRITY_BEFORE_NOT_OK")

    database = Database(str(db_path))
    refresh = (refresh_runtime_factory or build_official_index_refresh_runtime)(
        database=database, root=data_root)
    index_names = tuple(refresh.job.index_names)
    end = last_completed_session_date(now)
    newest = newest_official_bar_date(db_path, index_names)
    if newest is None:
        # Bootstrapping a range is an explicit manual acquisition, not cron.
        raise MaintenanceError("NO_ADMITTED_OFFICIAL_INDEX_BASELINE")

    result = {"last_completed_session_date": end.isoformat(),
              "newest_official_bar_before": newest.isoformat(),
              "fetch_range": None, "fetched_records_per_index": 0,
              "snapshot_date": None, "outcome": "UP_TO_DATE"}
    start = newest + timedelta(days=1)
    snapshot_date = now.astimezone(CAIRO).date()
    if start <= end and snapshot_date_used(db_path, index_names, snapshot_date):
        # Official index artifacts are immutable per (index, snapshot date). An
        # earlier admission today (e.g. a manual catch-up) owns this snapshot
        # date, so the next day's run admits these sessions instead.
        result["fetch_range"] = [start.isoformat(), end.isoformat()]
        result["outcome"] = "DEFERRED_SNAPSHOT_DATE_ALREADY_USED"
    elif start <= end:
        result["fetch_range"] = [start.isoformat(), end.isoformat()]
        provider = provider or EGXOfficialPublicProvider()
        records = probe(provider, index_names, start, end)
        result["fetched_records_per_index"] = records
        if records == 0:
            result["outcome"] = "NO_NEW_SESSIONS"
        else:
            result["snapshot_date"] = snapshot_date.isoformat()
            try:
                refresh.job.run(provider=provider, start_date=start, end_date=end,
                                snapshot_date=snapshot_date, page_size=1000)
            except Exception as exc:
                cause = exc.__cause__
                detail = f"{type(exc).__name__}:{exc}" + (
                    f":{type(cause).__name__}:{cause}" if cause is not None else "")
                raise MaintenanceError(f"REFRESH_FAILED:{detail}"[:300]) from exc
            result["outcome"] = "ADMITTED_NEW_SESSIONS"

    backfill_start = end - timedelta(days=BACKFILL_WINDOW_DAYS)
    backfill = (backfill_runtime_factory or build_calendar_backfill_runtime)(
        database=database, data_root=data_root)
    try:
        sessions = backfill.run_range(backfill_start, end)
    except Exception as exc:
        raise MaintenanceError(f"BACKFILL_FAILED:{type(exc).__name__}") from exc
    result["backfill_range"] = [backfill_start.isoformat(), end.isoformat()]
    result["verified_sessions_in_window"] = sum(
        1 for item in sessions if item.verification_status.value == "VERIFIED")

    after = inspect_database(db_path)
    result["integrity_after"] = after["integrity"]
    result["evidence_counts"] = {table: [before["counts"][table], after["counts"][table]]
                                 for table in EVIDENCE_TABLES}
    if after["integrity"] != "ok":
        raise MaintenanceError("INTEGRITY_AFTER_NOT_OK")
    changed = [table for table in LIFECYCLE_TABLES
               if before["counts"][table] != after["counts"][table]]
    if changed:
        raise MaintenanceError("LIFECYCLE_TABLES_CHANGED:" + ",".join(changed))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True,
                        help="lock, status and log directory (user-owned)")
    args = parser.parse_args(argv)
    for path in (args.db_path, args.data_root, args.state_dir):
        if not path.is_absolute():
            parser.error("all paths must be absolute")

    started = datetime.now(timezone.utc)
    record = {"job": "egx_official_calendar_maintenance", "started_at": started.isoformat(),
              "cairo_time": started.astimezone(CAIRO).isoformat(),
              "build_revision": os.getenv("EGX_BUILD_REVISION"),
              "live_money": False}
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir / "egx-calendar-maintenance.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            record.update(status="LOCKED", finished_at=datetime.now(timezone.utc).isoformat())
            _append_log(args.state_dir / "logs", record)
            print(json.dumps(record, sort_keys=True))
            return EXIT_LOCKED
        try:
            record.update(run(db_path=args.db_path, data_root=args.data_root, now=started))
            record["status"] = "SUCCESS"
            code = EXIT_OK
        except MaintenanceError as exc:
            record.update(status="FAILED", error=exc.code)
            code = EXIT_FAILED
        except Exception as exc:
            record.update(status="FAILED", error=f"UNEXPECTED:{type(exc).__name__}")
            code = EXIT_FAILED
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        _append_log(args.state_dir / "logs", record)
        _write_status(args.state_dir / "last-run.json", record)
        if record["status"] == "SUCCESS":
            _write_status(args.state_dir / "last-success.json", record)
    print(json.dumps(record, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
