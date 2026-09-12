"""Offline integrity and market-fact audit for an ER1C Tiingo bundle.

The audit deliberately stops short of canonical PIT admission.  It verifies the
captured bytes and reports source rows that require external market-state or
corporate-action evidence; it never infers those facts from vendor rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


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

MANIFEST_FIELDS = {
    "predeclaration_commit",
    "predeclaration_sha256",
    "provider",
    "purpose",
    "requests",
    "schema_version",
    "token_persisted",
}
REQUEST_FIELDS = {
    "completed_at_utc",
    "end_date",
    "endpoint",
    "error",
    "http_status",
    "provider",
    "raw_byte_size",
    "raw_file",
    "raw_sha256",
    "safe_response_headers",
    "start_date",
    "started_at_utc",
    "ticker",
}
EVIDENCE_MANIFEST_FIELDS = {"records", "schema_version"}
EVIDENCE_RECORD_FIELDS = {
    "byte_size",
    "error",
    "filename",
    "historical_availability_proven",
    "http_status",
    "retrieval_completed_at_utc",
    "retrieval_started_at_utc",
    "sha256",
    "source_url",
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


def require_exact_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{label} fields mismatch")


def safe_filename(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise ValueError(f"unsafe {label} filename")
    return value


def utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"invalid {label} timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid {label} timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"non-UTC {label} timestamp")
    return parsed


def validate_acquisition_manifest(manifest: Any) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise ValueError("acquisition manifest must be an object")
    require_exact_fields(manifest, MANIFEST_FIELDS, "acquisition manifest")
    if manifest["schema_version"] != "er1c-tiingo-acquisition-v1":
        raise ValueError("unsupported acquisition manifest schema")
    if manifest["provider"] != "Tiingo" or manifest["token_persisted"] is not False:
        raise ValueError("invalid acquisition manifest provider or token policy")
    if not isinstance(manifest["requests"], list) or not manifest["requests"]:
        raise ValueError("acquisition manifest requests must be nonempty")

    tickers: set[str] = set()
    files: set[str] = set()
    for request in manifest["requests"]:
        if not isinstance(request, dict):
            raise ValueError("acquisition request must be an object")
        require_exact_fields(request, REQUEST_FIELDS, "acquisition request")
        ticker = request["ticker"]
        filename = safe_filename(request["raw_file"], "raw")
        if not isinstance(ticker, str) or not ticker or ticker in tickers:
            raise ValueError("invalid or duplicate acquisition ticker")
        if filename in files:
            raise ValueError("duplicate acquisition raw filename")
        tickers.add(ticker)
        files.add(filename)
        if request["provider"] != manifest["provider"]:
            raise ValueError("acquisition request provider mismatch")
        if request["http_status"] != 200 or request["error"] is not None:
            raise ValueError("acquisition request was not successful")
        if type(request["raw_byte_size"]) is not int or request["raw_byte_size"] <= 0:
            raise ValueError("invalid acquisition raw byte size")
        started = utc_timestamp(request["started_at_utc"], "acquisition start")
        completed = utc_timestamp(request["completed_at_utc"], "acquisition completion")
        if completed < started:
            raise ValueError("acquisition completion precedes start")
        start = date.fromisoformat(request["start_date"])
        end = date.fromisoformat(request["end_date"])
        if end < start:
            raise ValueError("acquisition end date precedes start date")
        endpoint = urlparse(request["endpoint"])
        query = parse_qs(endpoint.query, strict_parsing=True)
        if (
            endpoint.scheme != "https"
            or endpoint.hostname != "api.tiingo.com"
            or endpoint.path != f"/tiingo/daily/{ticker}/prices"
            or query != {"startDate": [request["start_date"]], "endDate": [request["end_date"]]}
        ):
            raise ValueError("acquisition endpoint does not match request identity")
    return manifest["requests"]


def validate_evidence_manifest(manifest: Any) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise ValueError("evidence manifest must be an object")
    require_exact_fields(manifest, EVIDENCE_MANIFEST_FIELDS, "evidence manifest")
    if manifest["schema_version"] != "er1c-public-evidence-capture-v1":
        raise ValueError("unsupported evidence manifest schema")
    if not isinstance(manifest["records"], list) or not manifest["records"]:
        raise ValueError("evidence records must be nonempty")
    filenames: set[str] = set()
    urls: set[str] = set()
    for record in manifest["records"]:
        if not isinstance(record, dict):
            raise ValueError("evidence record must be an object")
        require_exact_fields(record, EVIDENCE_RECORD_FIELDS, "evidence record")
        filename = safe_filename(record["filename"], "evidence")
        source_url = record["source_url"]
        if filename in filenames or source_url in urls:
            raise ValueError("duplicate evidence record identity")
        filenames.add(filename)
        urls.add(source_url)
        parsed_url = urlparse(source_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            raise ValueError("invalid evidence source URL")
        if record["http_status"] != 200 or record["error"] is not None:
            raise ValueError("evidence retrieval was not successful")
        if type(record["byte_size"]) is not int or record["byte_size"] <= 0:
            raise ValueError("invalid evidence byte size")
        if type(record["historical_availability_proven"]) is not bool:
            raise ValueError("historical availability claim must be boolean")
        started = utc_timestamp(record["retrieval_started_at_utc"], "evidence retrieval start")
        completed = utc_timestamp(record["retrieval_completed_at_utc"], "evidence retrieval completion")
        if completed < started:
            raise ValueError("evidence retrieval completion precedes start")
    return manifest["records"]


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
    requests = validate_acquisition_manifest(manifest)
    predeclaration_sha256 = verify_predeclaration(
        predeclaration.read_bytes(), manifest["predeclaration_sha256"]
    )
    results: dict[str, Any] = {}
    for request in requests:
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
    evidence_records = validate_evidence_manifest(evidence_manifest)
    evidence_results = []
    for record in evidence_records:
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
