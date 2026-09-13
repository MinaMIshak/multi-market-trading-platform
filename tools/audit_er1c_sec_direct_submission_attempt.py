"""Audit a bounded negative SEC direct-submission acquisition attempt."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from tools.audit_er1c_sec_form_sample_declaration import audit_declaration
except ModuleNotFoundError:  # Support direct execution from the repository root.
    from audit_er1c_sec_form_sample_declaration import audit_declaration


FIELDS = {
    "client", "completed_at_utc", "declaration_sha256", "http_status",
    "record_rank_sha256", "response_body_bytes_observed",
    "response_bytes_retained", "result", "schema", "source_locator",
    "started_at_utc",
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate attempt field")
        result[key] = value
    return result


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} is not an explicit UTC timestamp")
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    if parsed.tzinfo is not timezone.utc:
        raise ValueError(f"{label} must use UTC")
    return parsed


def _direct_locator(record: dict) -> str:
    return "https://www.sec.gov/Archives/" + record["submission_locator"]


def audit_direct_attempt(
    attempt_path: Path, declaration_root: Path, index_root: Path,
) -> dict:
    declaration_result = audit_declaration(declaration_root, index_root)
    declaration = json.loads((declaration_root / "declaration.json").read_bytes())
    attempt = json.loads(attempt_path.read_bytes(), object_pairs_hook=_unique_object)
    if not isinstance(attempt, dict) or set(attempt) != FIELDS:
        raise ValueError("unexpected attempt schema")
    if attempt["schema"] != "er1c-sec-direct-submission-attempt-v1":
        raise ValueError("unexpected attempt version")
    first = declaration["records"][0]
    if attempt["declaration_sha256"] != declaration_result["declaration_sha256"]:
        raise ValueError("attempt does not bind frozen declaration")
    if attempt["record_rank_sha256"] != first["rank_sha256"]:
        raise ValueError("attempt does not bind first selected record")
    if attempt["source_locator"] != _direct_locator(first):
        raise ValueError("attempt locator is not the selected index locator")
    started = _utc(attempt["started_at_utc"], "attempt start")
    completed = _utc(attempt["completed_at_utc"], "attempt completion")
    if completed < started:
        raise ValueError("attempt completion precedes start")
    if attempt["client"] != "curl" or type(attempt["http_status"]) is not int:
        raise ValueError("invalid attempt transport result")
    if attempt["http_status"] != 403 or attempt["result"] != "HTTP_REJECTED":
        raise ValueError("record is not the declared negative HTTP result")
    observed = attempt["response_body_bytes_observed"]
    if (
        type(observed) is not int
        or observed < 0
        or attempt["response_bytes_retained"] is not False
    ):
        raise ValueError("invalid response-byte disposition")
    return {
        "attempt_sha256": hashlib.sha256(attempt_path.read_bytes()).hexdigest(),
        "declaration_sha256": declaration_result["declaration_sha256"],
        "first_selected_record_bound": True,
        "http_status": 403,
        "result": "NO_SUBMISSION_BYTES_ACQUIRED",
        "source_locator_kind": "SEC_QUARTERLY_INDEX_DIRECT_SUBMISSION",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("attempt", type=Path)
    parser.add_argument("declaration_root", type=Path)
    parser.add_argument("index_root", type=Path)
    args = parser.parse_args()
    result = audit_direct_attempt(args.attempt, args.declaration_root, args.index_root)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
