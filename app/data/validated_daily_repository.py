"""Approved read boundary for VALIDATED daily canonical artifacts.

The only consumer-facing reader of daily canonical files (the daily
counterpart of app/data/validated_index_repository.py). Consumers get
validated bars, never paths or raw files:
- the newest VALIDATED artifact per symbol for one provider (providers are
  never mixed);
- the artifact path must resolve inside the canonical root (no traversal or
  escape through links);
- the file's SHA-256 must equal the artifact record;
- rows are split into ``VALID_EXECUTABLE`` bars and a count of the rest
  (quarantined).
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3


class ValidatedDailyArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedDailyDataset:
    artifact: dict          # artifact record plus company name and ISIN; no path
    bars: list[dict]        # VALID_EXECUTABLE rows: date, open, high, low, close, volume (Decimal)
    quarantined: int


class ValidatedDailyArtifactRepository:
    def __init__(self, *, database_path: str | Path, data_root: str | Path) -> None:
        self.database_path = Path(database_path)
        self._root = (Path(data_root) / "canonical").resolve()

    def _resolve(self, relative: str) -> Path:
        candidate = (self._root / relative).resolve()
        if not candidate.is_relative_to(self._root):
            raise ValidatedDailyArtifactError("canonical artifact path escapes canonical root")
        return candidate

    def latest_by_symbol(self, provider: str) -> dict[str, dict]:
        uri = self.database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT a.*, i.name_en, i.source_symbol_code AS isin FROM daily_canonical_artifacts a "
                "JOIN canonical_instruments i ON i.instrument_id = a.instrument_id "
                "WHERE a.provider=? AND a.status='VALIDATED' "
                "ORDER BY a.canonical_symbol, a.source_snapshot_date, a.created_at", (provider,)).fetchall()
        return {row["canonical_symbol"]: dict(row) for row in rows}

    def load(self, artifact: dict) -> ValidatedDailyDataset:
        path = self._resolve(artifact["canonical_path"])
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
            raise ValidatedDailyArtifactError("ARTIFACT_HASH_MISMATCH")
        bars, quarantined = [], 0
        for row in json.loads(payload):
            if row.get("semantic_class") != "VALID_EXECUTABLE":
                quarantined += 1
                continue
            bars.append({"date": row["market_date"], **{key: Decimal(row[key]) for key in
                                                        ("open", "high", "low", "close", "volume")}})
        public = {key: value for key, value in artifact.items() if key != "canonical_path"}
        return ValidatedDailyDataset(public, sorted(bars, key=lambda bar: bar["date"]), quarantined)
