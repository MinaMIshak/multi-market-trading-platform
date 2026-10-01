"""TradingView EGX daily acquisition and backfill through the reviewed daily pipeline.

    python -m app.data.tradingview_refresh --db-path DB --data-root DATA --state-dir STATE \
        --tv-python /abs/tradingview-venv/bin/python [--market-watch-evidence DIR] \
        [--n-bars 750] [--lookback-days 1000] [--symbols T1,T2]

Stored bars carry provider ``tradingview_tvdatafeed_egx`` and whatever
admission the registry gives that provider. Acquisition never changes
admission. One run:
1. refuses to start unless ``PRAGMA integrity_check`` is ``ok``;
2. discovers TradingView's EGX stock listing, stores the raw pages hashed,
   writes the exact ISIN mapping report (app/data/provider_mapping.py) and
   re-applies aliases idempotently (provider ``tradingview_tvdatafeed_egx``,
   type ``TRADINGVIEW_SYMBOL``);
3. targets the latest **VERIFIED** EGX session that has completed in
   Africa/Cairo. Bars after it (an open session) are never stored;
4. runs one reviewed ``DailyRefreshJob`` per mapped symbol: raw bytes stored
   immutably, canonical validation with row quarantine, the admission policy
   (newest bar equals the session, at least ``--minimum-valid-bars``), and
   immutable artifacts. One symbol's failure never blocks another;
5. checks that the ISIN TradingView resolves for each symbol equals the mapped
   ISIN (otherwise ``REJECTED:RESOLVED_ISIN_MISMATCH``), and cross-checks the
   session bar against a completed-session official market-watch capture
   (DISCREPANCY quarantines the symbol, evidence kept);
6. re-checks integrity, fails if any lifecycle table changed, logs one JSON
   record and writes ``last-run.json`` / ``last-success.json`` plus a per-symbol
   outcomes file.
Exit codes: 0 completed, 1 failed, 75 locked.
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

from app.data.daily_cross_check import CrossCheckDiscrepancy, CrossCheckedProvider, load_completed_session
from app.data.official_calendar_maintenance import (
    CAIRO, LIFECYCLE_TABLES, _append_log, _write_status, last_completed_session_date,
)
from app.data.provider_mapping import apply_aliases, build_isin_mapping, known_equities, mapped_pairs
from app.data.providers.tradingview import PROVIDER, TradingViewError, TradingViewProvider, discover_equities
from app.data.twelve_data_refresh import _connect, inspect, latest_verified_session

ALIAS_TYPE = "TRADINGVIEW_SYMBOL"
EXIT_OK, EXIT_FAILED, EXIT_LOCKED = 0, 1, 75
FETCH_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "tradingview_fetch.py"


class RefreshStop(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class IsinVerifyingProvider:
    """Reject a series whose TradingView-resolved ISIN differs from the mapped ISIN."""

    def __init__(self, provider, isin_by_symbol):
        self.provider, self.isin_by_symbol = provider, isin_by_symbol

    @property
    def name(self):
        return self.provider.name

    def fetch_daily_bars(self, *, symbol, start_date, end_date):
        response = self.provider.fetch_daily_bars(symbol=symbol, start_date=start_date, end_date=end_date)
        resolved = (self.provider.resolved.get(symbol) or {}).get("isin")
        if resolved != self.isin_by_symbol.get(symbol):
            raise TradingViewError("RESOLVED_ISIN_MISMATCH")
        return response


def _store_pages(state_dir: Path, pages: list[bytes], now: datetime) -> list[dict]:
    directory = state_dir / "reference" / now.astimezone(CAIRO).date().isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    stored = []
    for index, payload in enumerate(pages, start=1):
        target = directory / f"symbol-search-page-{index:03d}.json"
        if not target.exists():
            target.write_bytes(payload)
            target.chmod(0o444)
        stored.append({"file": str(target), "sha256": hashlib.sha256(payload).hexdigest()})
    return stored


def run(*, db_path: Path, data_root: Path, state_dir: Path, now: datetime, provider,
        discover=None, lookback_days: int = 1000, minimum_valid_bars: int = 260,
        market_watch_evidence: Path | None = None, symbols: tuple[str, ...] | None = None) -> dict:
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

    if not db_path.is_file():
        raise RefreshStop("DATABASE_MISSING")
    before = inspect(db_path)
    if before["integrity"] != "ok":
        raise RefreshStop("INTEGRITY_BEFORE_NOT_OK")
    try:
        pages, rows = (discover or discover_equities)()
    except (TradingViewError, OSError) as exc:
        raise RefreshStop(f"DISCOVERY_FAILED:{getattr(exc, 'code', type(exc).__name__)}") from exc
    with closing(_connect(db_path, read_only=True)) as con:
        mapping = build_isin_mapping(rows, known_equities(con))
    mapping.update(generated_at=now.isoformat(), reference=_store_pages(state_dir, pages, now),
                   provider=PROVIDER)
    (state_dir / "mapping").mkdir(parents=True, exist_ok=True)
    _write_status(state_dir / "mapping" / "latest.json", mapping)
    with closing(_connect(db_path, read_only=False)) as con:
        inserted = apply_aliases(con, mapping, provider=PROVIDER, alias_type=ALIAS_TYPE)
        con.commit()
    session = latest_verified_session(db_path, last_completed_session_date(now))
    if session is None:
        raise RefreshStop("NO_VERIFIED_SESSION")
    start = session - timedelta(days=lookback_days)
    snapshot_date = now.astimezone(CAIRO).date()
    pairs = mapped_pairs(mapping)
    if symbols:
        pairs = [pair for pair in pairs if pair[0] in set(symbols)]
    if not pairs:
        raise RefreshStop("NO_TARGETS")
    isin_by_symbol = {m["provider_symbol"]: m["isin"] for m in mapping["matched"]}
    official = load_completed_session(market_watch_evidence, session)
    checked = CrossCheckedProvider(provider=IsinVerifyingProvider(provider, isin_by_symbol),
                                   session=session, official_by_isin=official,
                                   isin_by_symbol=isin_by_symbol,
                                   quarantine_dir=state_dir / "quarantine" / session.isoformat(),
                                   tolerance=Decimal("0.005"))
    database = Database(str(db_path))
    raw_store = ImmutableRawStore(data_root / "raw")
    resolver = SecurityMasterRepository(database)
    ingestor = DailyBarIngestor(raw_store=raw_store, repository=DataIngestionRepository(database),
                                resolver=resolver, admission_policy=DailyRefreshAdmissionPolicy(
                                    minimum_valid_bars=minimum_valid_bars))
    pipeline = DailyCanonicalPipeline(raw_store=raw_store)
    store = DailyCanonicalStore(data_root / "canonical")
    artifacts = DailyCanonicalArtifactRepository(database)
    outcomes = {}
    for ticker, provider_symbol in pairs:
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
            if isinstance(cause, CrossCheckDiscrepancy):
                outcomes[ticker] = "QUARANTINED_CROSS_CHECK"
            elif isinstance(cause, TradingViewError):
                outcomes[ticker] = f"REJECTED:{cause.code}"
            else:
                outcomes[ticker] = f"REJECTED:{exc.cause_type}:{str(cause)[:80]}"
    tally, verdicts = {}, {}
    for value in outcomes.values():
        key = "STORED_WITH_QUARANTINED_ROWS" if value.startswith("STORED_WITH") else value.split(":")[0]
        tally[key] = tally.get(key, 0) + 1
    for item in checked.results.values():
        verdicts[item["verdict"]] = verdicts.get(item["verdict"], 0) + 1
    result = {"provider": PROVIDER, "mapping": mapping["summary"], "aliases_inserted": inserted,
              "session": session.isoformat(), "window": [start.isoformat(), session.isoformat()],
              "snapshot_date": snapshot_date.isoformat(), "targets": len(pairs), "outcomes": tally,
              "cross_check": verdicts,
              "cross_check_source": "official market-watch capture" if official else "none available"}
    (state_dir / "outcomes").mkdir(parents=True, exist_ok=True)
    _write_status(state_dir / "outcomes" / f"{snapshot_date.isoformat()}.json",
                  {**result, "symbol_outcomes": outcomes, "cross_check_detail": checked.results,
                   "resolved": provider.resolved})
    after = inspect(db_path)
    result["integrity_after"] = after["integrity"]
    result["counts"] = {t: [before["counts"][t], after["counts"][t]] for t in before["counts"]}
    if after["integrity"] != "ok":
        raise RefreshStop("INTEGRITY_AFTER_NOT_OK")
    changed = [t for t in LIFECYCLE_TABLES if before["counts"][t] != after["counts"][t]]
    if changed:
        raise RefreshStop("LIFECYCLE_TABLES_CHANGED:" + ",".join(changed))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--tv-python", type=Path, required=True)
    parser.add_argument("--market-watch-evidence", type=Path)
    parser.add_argument("--n-bars", type=int, default=750)
    parser.add_argument("--lookback-days", type=int, default=1000)
    parser.add_argument("--minimum-valid-bars", type=int, default=260)
    parser.add_argument("--symbols")
    args = parser.parse_args(argv)
    for path in (args.db_path, args.data_root, args.state_dir, args.tv_python):
        if not path.is_absolute():
            parser.error("all paths must be absolute")
    started = datetime.now(timezone.utc)
    record = {"job": "tradingview_refresh", "started_at": started.isoformat(),
              "cairo_time": started.astimezone(CAIRO).isoformat(),
              "build_revision": os.getenv("EGX_BUILD_REVISION"), "live_money": False}
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir / "tradingview-refresh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            record.update(status="LOCKED", finished_at=datetime.now(timezone.utc).isoformat())
            _append_log(args.state_dir / "logs", record, prefix="tradingview-refresh")
            print(json.dumps(record, sort_keys=True))
            return EXIT_LOCKED
        code = EXIT_OK
        try:
            provider = TradingViewProvider(python_path=str(args.tv_python), fetch_script=str(FETCH_SCRIPT),
                                           evidence_dir=args.state_dir / "native", n_bars=args.n_bars)
            record.update(run(db_path=args.db_path, data_root=args.data_root, state_dir=args.state_dir,
                              now=started, provider=provider, lookback_days=args.lookback_days,
                              minimum_valid_bars=args.minimum_valid_bars,
                              market_watch_evidence=args.market_watch_evidence,
                              symbols=tuple(s.strip().upper() for s in args.symbols.split(","))
                              if args.symbols else None))
            record["status"] = "SUCCESS"
        except RefreshStop as exc:
            record.update(status="FAILED", error=exc.code)
            code = EXIT_FAILED
        except Exception as exc:
            record.update(status="FAILED", error=f"UNEXPECTED:{type(exc).__name__}")
            code = EXIT_FAILED
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        _append_log(args.state_dir / "logs", record, prefix="tradingview-refresh")
        _write_status(args.state_dir / "last-run.json", record)
        if record["status"] == "SUCCESS":
            _write_status(args.state_dir / "last-success.json", record)
    print(json.dumps(record, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
