"""Load validated, session-dated primary series for learning (EGX and US), read-only.

EGX: the latest VALIDATED TradingView artifact per symbol through the approved
validated reader. Sectors come from the official market-watch rows of the
newest stored capture (by ISIN; a static attribute, never price evidence).
US: the newest immutable canonical artifact per ticker written by
``app.us_run``, with sectors from the US ranking report.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

from app.data.validated_daily_repository import ValidatedDailyArtifactRepository
from app.learning.dataset import Series

EGX_PROVIDER = "tradingview_tvdatafeed_egx"


def egx_sectors(evidence_root):
    for directory in sorted(glob.glob(str(Path(evidence_root) / "*" / "*")), reverse=True):
        try:
            rows = []
            for page in sorted(Path(directory).glob("market-watch-page-*.json")):
                rows.extend(json.loads(page.read_text())["data"]["data"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        sectors = {r.get("isin"): (r.get("sector") or "").strip() or None for r in rows if r.get("isin")}
        if sectors:
            return sectors
    return {}


def load_egx(db_path, data_root, evidence_root):
    repository = ValidatedDailyArtifactRepository(database_path=db_path, data_root=data_root)
    sectors = egx_sectors(evidence_root) if evidence_root else {}
    out = []
    for ticker, artifact in sorted(repository.latest_by_symbol(EGX_PROVIDER).items()):
        dataset = repository.load(artifact)
        bars = [(b["date"], float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]), float(b["volume"]))
                for b in dataset.bars]
        out.append(Series(ticker, bars, isin=artifact.get("isin"), sector=sectors.get(artifact.get("isin")),
                          meta={"artifact_id": artifact["artifact_id"], "company": artifact.get("name_en")}))
    return out


def load_us(us_data_root, us_report_path):
    report = json.loads(Path(us_report_path).read_text()) if us_report_path and Path(us_report_path).is_file() else {}
    sectors = {r["ticker"]: r.get("sector") for r in report.get("symbols", [])}
    companies = {r["ticker"]: r.get("company") for r in report.get("symbols", [])}
    latest = {}
    for path in sorted(glob.glob(str(Path(us_data_root) / "us" / "canonical" / "*" / "*.json"))):
        latest[Path(path).stem] = path  # sessions sort ascending, so the newest wins
    out = []
    for stem, path in sorted(latest.items()):
        artifact = json.loads(Path(path).read_text())
        bars = [(r["date"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"]))
                for r in artifact["rows"] if r.get("volume") is not None]
        ticker = artifact["ticker"]
        out.append(Series(ticker, bars, isin=artifact.get("isin"), sector=sectors.get(ticker),
                          meta={"artifact": path, "company": companies.get(ticker)}))
    return out
