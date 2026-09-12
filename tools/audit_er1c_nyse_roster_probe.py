"""Offline audit for the bounded ER1C official NYSE roster-source probe."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


EXPECTED_FILES = {
    "nyse_symbol_mapping_index.html",
    "nyse_short_volume_20220701.txt",
    "nyse_short_volume_20221027.txt",
    "nyse_short_volume_20221028.txt",
}
EXPECTED_DATES = {"20220701", "20221027", "20221028"}
HEADER = "Date|Symbol|Short Exempt Volume|Short Volume|Total Volume|Market"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_manifest(root: Path) -> dict:
    raw = (root / "manifest.json").read_bytes()
    sidecar = (root / "manifest.sha256").read_text(encoding="ascii").strip()
    if sidecar != f"{_sha256(raw)}  manifest.json":
        raise ValueError("manifest SHA256 sidecar mismatch")
    manifest = json.loads(raw)
    if manifest.get("schema") != "er1c-nyse-roster-probe-v1":
        raise ValueError("unexpected manifest schema")
    records = manifest.get("records")
    if not isinstance(records, list) or len(records) != len(EXPECTED_FILES):
        raise ValueError("unexpected manifest records")
    return manifest


def audit_probe(root: Path) -> dict:
    manifest = _load_manifest(root)
    allowed = EXPECTED_FILES | {"manifest.json", "manifest.sha256"}
    actual = {item.name for item in root.iterdir() if item.is_file()}
    if actual != allowed:
        raise ValueError("probe inventory does not match manifest scope")

    by_name = {}
    for record in manifest["records"]:
        if not isinstance(record, dict) or set(record) != {
            "bytes", "content_type", "filename", "historical_availability_proven",
            "http_last_modified", "http_status", "receipt_utc", "sha256",
            "source_locator",
        }:
            raise ValueError("unexpected evidence-record schema")
        filename = record["filename"]
        if filename in by_name or filename not in EXPECTED_FILES:
            raise ValueError("unexpected or duplicate evidence filename")
        if record["http_status"] != 200 or record["historical_availability_proven"] is not False:
            raise ValueError("invalid retrieval or availability classification")
        payload = (root / filename).read_bytes()
        if record["bytes"] != len(payload) or record["sha256"] != _sha256(payload):
            raise ValueError(f"artifact integrity mismatch: {filename}")
        by_name[filename] = payload

    index = by_name["nyse_symbol_mapping_index.html"].decode("utf-8")
    if "Index of /NYSESymbolMapping/" not in index:
        raise ValueError("symbol-mapping index scope mismatch")
    if "20220701" in index or "20221027" in index or "20221028" in index:
        raise ValueError("unexpected pilot-date symbol mapping in retained index")

    observations = {}
    for filename, payload in by_name.items():
        if not filename.startswith("nyse_short_volume_"):
            continue
        lines = payload.decode("ascii").splitlines()
        if not lines or lines[0] != HEADER:
            raise ValueError(f"short-volume header mismatch: {filename}")
        expected_date = filename.removeprefix("nyse_short_volume_").removesuffix(".txt")
        rows = {}
        for number, line in enumerate(lines[1:], start=2):
            fields = line.split("|")
            if len(fields) != 6 or fields[0] != expected_date:
                raise ValueError(f"malformed short-volume row: {filename}:{number}")
            _, symbol, short_exempt, short, total, market = fields
            if not symbol or (market, symbol) in rows:
                raise ValueError(f"invalid or duplicate symbol row: {filename}:{number}")
            volumes = tuple(int(value) for value in (short_exempt, short, total))
            if any(value < 0 for value in volumes) or not (
                volumes[0] <= volumes[1] <= volumes[2]
            ):
                raise ValueError(f"incoherent volume row: {filename}:{number}")
            rows[(market, symbol)] = {"line": number, "total_volume": volumes[2]}
        observations[expected_date] = {
            "rows": len(rows),
            "IBM": rows.get(("N", "IBM")),
            "TWTR": rows.get(("N", "TWTR")),
        }
    if set(observations) != EXPECTED_DATES:
        raise ValueError("short-volume date scope mismatch")

    return {
        "source_classification": "AUTHORITATIVE_WITHIN_DAILY_SHORT_SALE_VOLUME_SCOPE",
        "historical_xnys_universe": "NO_GO",
        "observations": observations,
        "limitations": [
            "short-sale volume reports contain reported trading activity, not complete listing rosters",
            "absence from a report does not prove closure, suspension, delisting, or ineligibility",
            "the retained current symbol-mapping index exposes no pilot-date edition",
            "current receipt does not prove historical pre-decision availability",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_probe(args.root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
