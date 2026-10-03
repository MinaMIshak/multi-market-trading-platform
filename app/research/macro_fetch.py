"""Fetch macro series (public FRED CSV), store raw immutably, write the report.

python -m app.research.macro_fetch --data-root <abs> --report <abs>/macro-context.json

Raw bytes land under <data-root>/macro/raw/<series>/<sha256>.csv before
parsing (content-addressed, never overwritten). The report is replaced
atomically. A failed series is UNAVAILABLE with its cause; nothing is reused
from an earlier run or invented. Exit 0 if at least one series is available.
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.research.macro import HISTORY_DAYS, SERIES, URL, build_report

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 30


def http_get(url):
    if not url.startswith("https://"):
        raise ValueError("https required")
    request = urllib.request.Request(url, headers={"User-Agent": "egx-paper-shadow-research/1"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("response too large")
    return payload


def store_raw(data_root, series_id, payload):
    digest = hashlib.sha256(payload).hexdigest()
    folder = Path(data_root) / "macro" / "raw" / series_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.csv"
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"raw store corruption at {path}")
    return digest, str(path)


def fetch_all(data_root, *, today, now, get=http_get):
    start = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    fetched = {}
    for series in SERIES:
        url = URL.format(series=series.series_id, start=start)
        try:
            payload = get(url)
            digest, path = store_raw(data_root, series.series_id, payload)
            fetched[series.series_id] = {"text": payload.decode("utf-8"), "sha256": digest, "raw_path": path,
                                         "retrieved_at": now().isoformat(), "url": url}
        except Exception as exc:  # recorded per series; the report shows the cause
            fetched[series.series_id] = {"error": f"{type(exc).__name__}: {exc}", "url": url}
    return fetched


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main(argv=None, *, get=http_get, now=lambda: datetime.now(timezone.utc)):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    if not (Path(args.data_root).is_absolute() and Path(args.report).is_absolute()):
        parser.error("paths must be absolute")
    started = now()
    fetched = fetch_all(args.data_root, today=started.date(), now=now, get=get)
    report = build_report(fetched, as_of=started.date(), generated_at=started)
    write_report(args.report, report)
    available = sum(1 for row in report["series"] if row["status"] == "AVAILABLE")
    print(json.dumps({"macro_series_available": available, "of": len(report["series"]),
                      "report": args.report, "live_money": False}))
    return 0 if available else 1


if __name__ == "__main__":
    sys.exit(main())
