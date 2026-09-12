"""Offline integrity and market-fact audit for an ER1C Tiingo bundle.

The audit deliberately stops short of canonical PIT admission.  It verifies the
captured bytes and reports source rows that require external market-state or
corporate-action evidence; it never infers those facts from vendor rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


RAW_FIELDS = {
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "divCash",
    "splitFactor",
}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_sha256_sidecar(payload: bytes, sidecar: bytes, filename: str) -> str:
    """Verify a conventional SHA256 sidecar without trusting its label."""
    try:
        line = sidecar.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ValueError(f"invalid SHA256 sidecar: {filename}") from error
    fields = line.split()
    digest = sha256_bytes(payload)
    if len(fields) != 2 or fields[0] != digest:
        raise ValueError(f"SHA256 sidecar mismatch: {filename}")
    return digest


def verify_predeclaration(payload: bytes, expected_sha256: str) -> str:
    digest = sha256_bytes(payload)
    if digest != expected_sha256:
        raise ValueError("frozen predeclaration SHA256 mismatch")
    return digest


def market_date(value: str) -> date:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.time().isoformat() != "00:00:00":
        raise ValueError(f"non-midnight Tiingo daily timestamp: {value}")
    return parsed.date()


def audit_rows(payload: bytes, start: date, end: date) -> dict[str, Any]:
    rows = json.loads(payload)
    if not isinstance(rows, list) or not rows:
        raise ValueError("raw response must be a nonempty JSON array")

    seen: set[date] = set()
    zero_volume: list[str] = []
    action_markers: list[dict[str, Any]] = []
    previous: date | None = None
    for number, row in enumerate(rows, start=1):
        missing = RAW_FIELDS.difference(row)
        if missing:
            raise ValueError(f"row {number} missing fields: {sorted(missing)}")
        day = market_date(row["date"])
        if day < start or day > end:
            raise ValueError(f"row {number} outside declared request bounds")
        if day in seen or (previous is not None and day <= previous):
            raise ValueError(f"duplicate or unordered market date at row {number}")
        seen.add(day)
        previous = day

        prices = tuple(row[field] for field in ("open", "high", "low", "close"))
        if any(type(value) not in (int, float) or value <= 0 for value in prices):
            raise ValueError(f"row {number} has nonpositive raw price")
        if row["low"] > min(row["open"], row["close"], row["high"]):
            raise ValueError(f"row {number} has incoherent raw low")
        if row["high"] < max(row["open"], row["close"], row["low"]):
            raise ValueError(f"row {number} has incoherent raw high")
        if type(row["volume"]) not in (int, float) or row["volume"] < 0:
            raise ValueError(f"row {number} has invalid volume")
        if row["volume"] == 0:
            zero_volume.append(day.isoformat())
        if row["divCash"] != 0 or row["splitFactor"] != 1:
            action_markers.append(
                {
                    "date": day.isoformat(),
                    "divCash": row["divCash"],
                    "splitFactor": row["splitFactor"],
                    "row_number": number,
                }
            )

    return {
        "row_count": len(rows),
        "first_market_date": min(seen).isoformat(),
        "last_market_date": max(seen).isoformat(),
        "zero_volume_dates_requiring_external_classification": zero_volume,
        "vendor_action_markers_not_action_coverage": action_markers,
    }


def audit_bundle(bundle: Path, predeclaration: Path) -> dict[str, Any]:
    manifest_payload = (bundle / "manifest.json").read_bytes()
    manifest_sha256 = verify_sha256_sidecar(
        manifest_payload,
        (bundle / "manifest.sha256").read_bytes(),
        "manifest.sha256",
    )
    manifest = json.loads(manifest_payload)
    predeclaration_sha256 = verify_predeclaration(
        predeclaration.read_bytes(), manifest["predeclaration_sha256"]
    )
    results: dict[str, Any] = {}
    for request in manifest["requests"]:
        raw_path = bundle / request["raw_file"]
        payload = raw_path.read_bytes()
        if len(payload) != request["raw_byte_size"]:
            raise ValueError(f"byte-size mismatch: {raw_path.name}")
        if sha256_bytes(payload) != request["raw_sha256"]:
            raise ValueError(f"SHA256 mismatch: {raw_path.name}")
        results[request["ticker"]] = {
            "raw_file": raw_path.name,
            "raw_byte_size": len(payload),
            "raw_sha256": sha256_bytes(payload),
            **audit_rows(
                payload,
                date.fromisoformat(request["start_date"]),
                date.fromisoformat(request["end_date"]),
            ),
        }

    evidence_manifest_path = bundle / "evidence" / "evidence_manifest.json"
    evidence_manifest_payload = evidence_manifest_path.read_bytes()
    evidence_manifest_sha256 = verify_sha256_sidecar(
        evidence_manifest_payload,
        (evidence_manifest_path.parent / "evidence_manifest.sha256").read_bytes(),
        "evidence_manifest.sha256",
    )
    evidence_manifest = json.loads(evidence_manifest_payload)
    evidence_results = []
    for record in evidence_manifest["records"]:
        payload = (evidence_manifest_path.parent / record["filename"]).read_bytes()
        if len(payload) != record["byte_size"]:
            raise ValueError(f"evidence byte-size mismatch: {record['filename']}")
        if sha256_bytes(payload) != record["sha256"]:
            raise ValueError(f"evidence SHA256 mismatch: {record['filename']}")
        evidence_results.append(
            {
                "filename": record["filename"],
                "byte_size": len(payload),
                "sha256": sha256_bytes(payload),
                "historical_availability_proven": record[
                    "historical_availability_proven"
                ],
            }
        )

    return {
        "schema_version": "er1c-offline-audit-v1",
        "bundle": str(bundle),
        "manifest_sha256": manifest_sha256,
        "predeclaration_commit": manifest["predeclaration_commit"],
        "predeclaration_sha256": predeclaration_sha256,
        "frozen_predeclaration_verified": True,
        "prices": results,
        "public_evidence_manifest_sha256": evidence_manifest_sha256,
        "public_evidence": evidence_results,
        "canonical_pit_admission": "NO_GO",
        "admission_blockers": [
            "source historical availability is not proven",
            "approved human review bindings are absent",
            "complete exact-date XNYS universe evidence is absent",
            "exact-date identity evidence across the interval is absent",
            "complete bounded corporate-action coverage is absent",
            "every-calendar-date evidenced XNYS session records are absent",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument(
        "--predeclaration",
        type=Path,
        default=Path("docs/ER1C_US_PILOT_PREDECLARATION.md"),
        help="frozen declaration whose bytes must match the acquisition manifest",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            audit_bundle(args.bundle, args.predeclaration), indent=2, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
