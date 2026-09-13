"""Audit the retained NYSE symbol-mapping documentation qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

PURPOSE = "official publication-scope and timing qualification only"
EXPECTED = {
    "nyse_bqt_common_client_spec_v2.2l.pdf": (
        "https://www.nyse.com/publicdocs/nyse/data/NYSE_Pillar_BQT_Common_Client_Specification_v2.2l.pdf",
        679215,
        "ec8edcad77cd201fcd5645f706360185b73c95ca3b28c86a325a768c83690098",
    ),
    "nyse_symbol_mapping_filename_change_march2022.pdf": (
        "https://www.nyse.com/publicdocs/nyse/notifications/trader-update/110000415198/NYSE_Group_SymbolMapping_FilenameChange_March2022.pdf",
        131731,
        "94284ee3abc9328b2647b839e64b0d82cb1c9942b1b4938e6b565dea8ab67846",
    ),
}


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} is not a timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{label} is not explicit UTC")
    return parsed.astimezone(timezone.utc)


def audit_symbol_mapping_spec(root: Path, *, audited_at: datetime | None = None) -> dict:
    """Bind conclusions to exact reviewed documents and fail closed on data use."""
    audited_at = audited_at or datetime.now(timezone.utc)
    if type(audited_at) is not datetime or audited_at.tzinfo is None:
        raise ValueError("audited_at must be timezone-aware")
    audited_at = audited_at.astimezone(timezone.utc)
    raw = (root / "manifest.json").read_bytes()
    sidecar = (root / "manifest.sha256").read_text(encoding="ascii").strip()
    if sidecar != f"{_sha(raw)}  manifest.json":
        raise ValueError("manifest SHA256 sidecar mismatch")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "purpose", "records"}:
        raise ValueError("unexpected manifest fields")
    if manifest["schema"] != "er1c-nyse-symbol-mapping-spec-v1" or manifest["purpose"] != PURPOSE:
        raise ValueError("unexpected manifest scope")
    records = manifest["records"]
    if not isinstance(records, list) or len(records) != len(EXPECTED):
        raise ValueError("unexpected artifact records")
    inventory = {path.name for path in root.iterdir() if path.is_file()}
    if inventory != set(EXPECTED) | {"manifest.json", "manifest.sha256"}:
        raise ValueError("artifact inventory does not match bounded scope")
    required = {
        "filename", "source_locator", "resolved_locator", "http_status",
        "content_type", "http_last_modified", "receipt_started_utc",
        "receipt_completed_utc", "bytes", "sha256", "historical_availability_proven",
    }
    receipts, seen = [], set()
    for record in records:
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError("unexpected artifact-record schema")
        name = record["filename"]
        if name in seen or name not in EXPECTED:
            raise ValueError("unexpected or duplicate artifact")
        seen.add(name)
        locator, size, digest = EXPECTED[name]
        if record["source_locator"] != locator or record["resolved_locator"] != locator:
            raise ValueError("unexpected locator or redirect")
        if record["http_status"] != 200 or record["historical_availability_proven"] is not False:
            raise ValueError("retrieval status or historical-availability claim changed")
        if not isinstance(record["content_type"], str) or "application/pdf" not in record["content_type"]:
            raise ValueError("unexpected media type")
        started = _utc(record["receipt_started_utc"], "request start")
        completed = _utc(record["receipt_completed_utc"], "receipt completion")
        if completed < started or completed > audited_at:
            raise ValueError("invalid artifact receipt ordering")
        payload = (root / name).read_bytes()
        if (record["bytes"], record["sha256"]) != (size, digest):
            raise ValueError(f"reviewed edition mismatch: {name}")
        if len(payload) != size or _sha(payload) != digest or not payload.startswith(b"%PDF-"):
            raise ValueError(f"artifact integrity mismatch: {name}")
        receipts.append(completed)
    return {
        "documentation_integrity": "PASS",
        "latest_receipt_at": max(receipts).isoformat(),
        "market_and_listing_fields_documented": True,
        "common_stock_field_documented_in_reviewed_layout": False,
        "stable_instrument_identity_proven": False,
        "pilot_date_mapping_files_retained": False,
        "archive_completeness_proven": False,
        "predecision_availability_proven": False,
        "retention_and_transformation_permission": "UNRESOLVED",
        "mapping_data_requests_made": 0,
        "canonical_us1_identity": "NO_GO",
        "canonical_us5a_universe": "NO_GO",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_symbol_mapping_spec(args.root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
