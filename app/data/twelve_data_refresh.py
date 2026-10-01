"""Twelve Data XCAI daily acquisition and backfill through the reviewed daily pipeline.

    python -m app.data.twelve_data_refresh --mode daily|backfill --db-path DB \
        --data-root DATA --state-dir STATE [--start-date YYYY-MM-DD] \
        [--market-watch-evidence DIR] [--symbols T1,T2]

The key is read from the file named by ``EGX_TWELVE_DATA_API_KEY_FILE``
(a regular file that only its owner can read), or else from
``TWELVE_DATA_API_KEY``. It is never logged.

One run:
1. Refuses to start unless ``PRAGMA integrity_check`` is ``ok``.
2. Fetches the provider's XCAI reference list, stores it raw and hashed, and
   writes the exact ISIN mapping report (app/data/twelve_data_mapping.py).
3. Without a key it stops here with ``NO_CREDENTIALS`` (exit 2). The mapping
   is still produced, so supplying the key is the only remaining step.
4. With a key it re-applies the provider aliases idempotently, and targets the
   latest **VERIFIED** EGX session that has completed in Africa/Cairo (never
   weekday arithmetic, never an open session). Daily mode requests the
   trailing ``--lookback-days``; backfill requests from ``--start-date``.
5. Prefetches in credit-limited batches, then runs one reviewed
   ``DailyRefreshJob`` per symbol: raw bytes stored immutably, canonical
   validation with row quarantine, the admission policy (newest bar equals the
   session, minimum valid bars), and immutable artifacts. One symbol's failure
   never blocks another. Artifacts are idempotent per (symbol, snapshot date).
6. Where a completed-session official market-watch capture exists, the
   session bar is cross-checked. A material discrepancy quarantines the
   symbol (evidence kept; nothing ingested).
7. Re-checks integrity and fails if any lifecycle table changed. Logs one
   JSON record per run, and writes ``last-run.json`` / ``last-success.json``.

Stored bars stay EVIDENCE_BLOCKED for candidates until the operator records
the subscription and usage-rights review in app/data/source_admission.py.
Exit codes: 0 completed, 1 failed, 2 no credentials, 75 locked.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import fcntl
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from app.data.daily_cross_check import CrossCheckDiscrepancy, CrossCheckedProvider, load_completed_session
from app.data.official_calendar_maintenance import (
    CAIRO, LIFECYCLE_TABLES, _append_log, _write_status, last_completed_session_date,
)
from app.data.providers.twelve_data import TwelveDataError, TwelveDataProvider
from app.data.twelve_data_mapping import apply_aliases, build_mapping, known_equities, targets

EXIT_OK, EXIT_FAILED, EXIT_NO_CREDENTIALS, EXIT_LOCKED = 0, 1, 2, 75
COUNTED = (*LIFECYCLE_TABLES, "daily_canonical_artifacts", "data_ingestions", "instrument_aliases")


class RefreshStop(RuntimeError):
    def __init__(self, code: str, exit_code: int = EXIT_FAILED):
        super().__init__(code)
        self.code, self.exit_code = code, exit_code


def read_api_key() -> str | None:
    path = os.getenv("EGX_TWELVE_DATA_API_KEY_FILE")
    if path:
        key_file = Path(path)
        if not key_file.is_absolute() or key_file.is_symlink() or not key_file.is_file():
            raise RefreshStop("API_KEY_FILE_INVALID")
        info = key_file.stat()
        if info.st_uid != os.getuid() or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise RefreshStop("API_KEY_FILE_PERMISSIONS")
        key = key_file.read_text().strip()
        return key or None
    return (os.getenv("TWELVE_DATA_API_KEY") or "").strip() or None


def _connect(db_path: Path, *, read_only: bool) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + ("?mode=ro" if read_only else "")
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def inspect(db_path: Path) -> dict:
    with closing(_connect(db_path, read_only=True)) as con:
        counts = {}
        for table in COUNTED:
            try:
                counts[table] = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.OperationalError:
                counts[table] = None
        return {"integrity": con.execute("PRAGMA integrity_check").fetchone()[0], "counts": counts}


def latest_verified_session(db_path: Path, on_or_before: date) -> date | None:
    with closing(_connect(db_path, read_only=True)) as con:
        row = con.execute("SELECT MAX(market_date) FROM market_sessions WHERE status='VERIFIED' "
                          "AND market_date <= ?", (on_or_before.isoformat(),)).fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def _store_reference(state_dir: Path, response, now: datetime) -> dict:
    directory = state_dir / "reference"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{now.astimezone(CAIRO).date().isoformat()}-{response.filename}"
    target = directory / name
    if not target.exists():
        target.write_bytes(response.payload)
        target.chmod(0o444)
    return {"file": str(target), "sha256": hashlib.sha256(response.payload).hexdigest(),
            "source_uri": response.source_uri, "records": response.record_count}


def run(*, db_path: Path, data_root: Path, state_dir: Path, mode: str, now: datetime,
        start_date: date | None = None, lookback_days: int = 400, minimum_valid_bars: int = 260,
        market_watch_evidence: Path | None = None, symbols: tuple[str, ...] | None = None,
        provider: TwelveDataProvider | None = None, api_key: str | None = None) -> dict:
    from app.data.daily_canonical_pipeline import DailyCanonicalPipeline
    from app.data.daily_canonical_store import DailyCanonicalStore
    from app.data.daily_ingestion import DailyBarIngestor
    from app.data.daily_refresh_admission import DailyRefreshAdmissionPolicy
    from app.data.daily_refresh_job import DailyRefreshJob, DailyRefreshJobError, DailyRefreshTarget
    from app.data.ingestion_repository import DataIngestionRepository
    from app.data.raw_store import ImmutableRawStore
    from app.egx_refresh_mapping import require_refresh_targets
    from app.storage.daily_canonical_artifact_repository import DailyCanonicalArtifactRepository
    from app.storage.database import Database
    from app.storage.security_master_repository import SecurityMasterRepository

    if mode not in ("daily", "backfill"):
        raise RefreshStop("UNSUPPORTED_MODE")
    if mode == "backfill" and start_date is None:
        raise RefreshStop("BACKFILL_REQUIRES_START_DATE")
    if not db_path.is_file():
        raise RefreshStop("DATABASE_MISSING")
    before = inspect(db_path)
    if before["integrity"] != "ok":
        raise RefreshStop("INTEGRITY_BEFORE_NOT_OK")

    provider = provider or TwelveDataProvider(api_key=api_key)
    try:
        reference_response, reference_rows = provider.fetch_reference_equities()
    except TwelveDataError as exc:
        raise RefreshStop(f"REFERENCE_FAILED:{exc.code}") from exc
    with closing(_connect(db_path, read_only=True)) as con:
        mapping = build_mapping(reference_rows, known_equities(con))
    mapping_dir = state_dir / "mapping"
    mapping_dir.mkdir(parents=True, exist_ok=True)
    mapping["generated_at"] = now.isoformat()
    mapping["reference"] = _store_reference(state_dir, reference_response, now)
    _write_status(mapping_dir / "latest.json", mapping)
    result = {"mode": mode, "mapping": mapping["summary"], "reference": mapping["reference"]}
    if not provider.api_key:
        return {**result, "outcome": "NO_CREDENTIALS"}

    with closing(_connect(db_path, read_only=False)) as con:
        result["aliases_inserted"] = apply_aliases(con, mapping)
        con.commit()

    session = latest_verified_session(db_path, last_completed_session_date(now))
    if session is None:
        raise RefreshStop("NO_VERIFIED_SESSION")
    snapshot_date = now.astimezone(CAIRO).date()
    start = start_date if mode == "backfill" else session - timedelta(days=lookback_days)
    if start > session:
        raise RefreshStop("START_AFTER_SESSION")
    pairs = targets(mapping)
    if symbols:
        wanted = set(symbols)
        pairs = [pair for pair in pairs if pair[0] in wanted]
    if not pairs:
        raise RefreshStop("NO_TARGETS")
    result.update(session=session.isoformat(), window=[start.isoformat(), session.isoformat()],
                  snapshot_date=snapshot_date.isoformat(), targets=len(pairs))

    try:
        fetch_failures = provider.prefetch([p for _, p in pairs], start, session)
    except TwelveDataError as exc:
        raise RefreshStop(f"FETCH_FAILED:{exc.code}") from exc
    official = load_completed_session(market_watch_evidence, session)
    isin_by_symbol = {item["provider_symbol"]: item["isin"]
                      for item in mapping["matched_exact"] + mapping["matched_suffix"]}
    checked = CrossCheckedProvider(provider=provider, session=session, official_by_isin=official,
                                   isin_by_symbol=isin_by_symbol,
                                   quarantine_dir=state_dir / "quarantine" / session.isoformat(),
                                   tolerance=Decimal("0.005"))
    database = Database(str(db_path))
    raw_store = ImmutableRawStore(data_root / "raw")
    resolver = SecurityMasterRepository(database)
    ingestor = DailyBarIngestor(raw_store=raw_store, repository=DataIngestionRepository(database),
                                resolver=resolver,
                                admission_policy=DailyRefreshAdmissionPolicy(
                                    minimum_valid_bars=minimum_valid_bars))
    pipeline = DailyCanonicalPipeline(raw_store=raw_store)
    store = DailyCanonicalStore(data_root / "canonical")
    artifacts = DailyCanonicalArtifactRepository(database)
    outcomes = {}
    for ticker, provider_symbol in pairs:
        if provider_symbol in fetch_failures:
            outcomes[ticker] = f"FETCH_FAILED:{fetch_failures[provider_symbol]}"
            continue
        job = DailyRefreshJob(ingestor=ingestor, pipeline=pipeline, canonical_store=store,
                              artifact_repository=artifacts,
                              targets=(DailyRefreshTarget(ticker, provider_symbol),),
                              target_admission=partial(require_refresh_targets, resolver))
        try:
            item = job.run(provider=checked, start_date=start, end_date=session,
                           snapshot_date=snapshot_date).items[0]
            outcomes[ticker] = ("STORED" if item.quarantined_bar_count == 0
                                else f"STORED_WITH_{item.quarantined_bar_count}_QUARANTINED_ROWS")
        except DailyRefreshJobError as exc:
            cause = exc.__cause__
            outcomes[ticker] = ("QUARANTINED_CROSS_CHECK" if isinstance(cause, CrossCheckDiscrepancy)
                                else f"REJECTED:{exc.cause_type}")
    tally = {}
    for value in outcomes.values():
        key = value.split(":")[0] if not value.startswith("STORED_WITH") else "STORED_WITH_QUARANTINED_ROWS"
        tally[key] = tally.get(key, 0) + 1
    verdicts = {}
    for item in checked.results.values():
        verdicts[item["verdict"]] = verdicts.get(item["verdict"], 0) + 1
    result.update(outcomes=tally, cross_check=verdicts,
                  cross_check_source="official market-watch capture" if official else "none available",
                  credits_remaining=provider.limiter.remaining)
    (state_dir / "outcomes").mkdir(parents=True, exist_ok=True)
    _write_status(state_dir / "outcomes" / f"{snapshot_date.isoformat()}-{mode}.json",
                  {**result, "symbol_outcomes": outcomes, "cross_check_detail": checked.results})

    after = inspect(db_path)
    result["integrity_after"] = after["integrity"]
    result["counts"] = {table: [before["counts"][table], after["counts"][table]] for table in COUNTED}
    if after["integrity"] != "ok":
        raise RefreshStop("INTEGRITY_AFTER_NOT_OK")
    changed = [t for t in LIFECYCLE_TABLES if before["counts"][t] != after["counts"][t]]
    if changed:
        raise RefreshStop("LIFECYCLE_TABLES_CHANGED:" + ",".join(changed))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mode", choices=("daily", "backfill"), required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--lookback-days", type=int, default=400)
    parser.add_argument("--minimum-valid-bars", type=int, default=260)
    parser.add_argument("--market-watch-evidence", type=Path)
    parser.add_argument("--symbols", help="comma-separated canonical tickers (subset)")
    parser.add_argument("--credits-per-minute", type=int, default=8)
    parser.add_argument("--daily-credit-budget", type=int, default=800)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args(argv)
    for path in (args.db_path, args.data_root, args.state_dir):
        if not path.is_absolute():
            parser.error("all paths must be absolute")
    started = datetime.now(timezone.utc)
    record = {"job": "twelve_data_refresh", "started_at": started.isoformat(),
              "cairo_time": started.astimezone(CAIRO).isoformat(), "mode": args.mode,
              "build_revision": os.getenv("EGX_BUILD_REVISION"), "live_money": False}
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir / "twelve-data-refresh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            record.update(status="LOCKED", finished_at=datetime.now(timezone.utc).isoformat())
            _append_log(args.state_dir / "logs", record, prefix="twelve-data-refresh")
            print(json.dumps(record, sort_keys=True))
            return EXIT_LOCKED
        code = EXIT_OK
        try:
            provider = TwelveDataProvider(api_key=read_api_key(), batch_size=args.batch_size,
                                          credits_per_minute=args.credits_per_minute,
                                          daily_credit_budget=args.daily_credit_budget)
            record.update(run(db_path=args.db_path, data_root=args.data_root,
                              state_dir=args.state_dir, mode=args.mode, now=started,
                              start_date=args.start_date, lookback_days=args.lookback_days,
                              minimum_valid_bars=args.minimum_valid_bars,
                              market_watch_evidence=args.market_watch_evidence,
                              symbols=tuple(s.strip().upper() for s in args.symbols.split(","))
                              if args.symbols else None, provider=provider))
            if record.get("outcome") == "NO_CREDENTIALS":
                record["status"] = "NO_CREDENTIALS"
                code = EXIT_NO_CREDENTIALS
            else:
                record["status"] = "SUCCESS"
        except RefreshStop as exc:
            record.update(status="NO_CREDENTIALS" if exc.exit_code == EXIT_NO_CREDENTIALS else "FAILED",
                          error=exc.code)
            code = exc.exit_code
        except Exception as exc:
            record.update(status="FAILED", error=f"UNEXPECTED:{type(exc).__name__}")
            code = EXIT_FAILED
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        _append_log(args.state_dir / "logs", record, prefix="twelve-data-refresh")
        _write_status(args.state_dir / "last-run.json", record)
        if record["status"] == "SUCCESS":
            _write_status(args.state_dir / "last-success.json", record)
    print(json.dumps({k: record[k] for k in record if k not in ("reference",)}, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
