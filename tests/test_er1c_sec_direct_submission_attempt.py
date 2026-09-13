import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_er1c_sec_form_sample_declaration import _write_fixture
from tools.audit_er1c_sec_direct_submission_attempt import audit_direct_attempt


AUDITED_AT = datetime(2026, 9, 13, 2, tzinfo=timezone.utc)


def _attempt(tmp_path):
    declaration, indexes = _write_fixture(tmp_path)
    raw_declaration = (declaration / "declaration.json").read_bytes()
    first = json.loads(raw_declaration)["records"][0]
    payload = {
        "schema": "er1c-sec-direct-submission-attempt-v1",
        "declaration_sha256": hashlib.sha256(raw_declaration).hexdigest(),
        "record_rank_sha256": first["rank_sha256"],
        "source_locator": "https://www.sec.gov/Archives/" + first["submission_locator"],
        "client": "curl", "http_status": 403,
        "started_at_utc": "2026-09-13T01:04:03.668868Z",
        "completed_at_utc": "2026-09-13T01:04:03.737899Z",
        "response_body_bytes_observed": 4818,
        "response_bytes_retained": False, "result": "HTTP_REJECTED",
    }
    path = tmp_path / "attempt.json"
    path.write_text(json.dumps(payload, sort_keys=True))
    return path, declaration, indexes


def test_audits_negative_attempt_against_frozen_first_record(tmp_path):
    path, declaration, indexes = _attempt(tmp_path)
    result = audit_direct_attempt(path, declaration, indexes, audited_at=AUDITED_AT)
    assert result["first_selected_record_bound"] is True
    assert result["result"] == "NO_SUBMISSION_BYTES_ACQUIRED"


@pytest.mark.parametrize(
    "field", ["declaration_sha256", "record_rank_sha256", "source_locator"],
)
def test_rejects_sample_or_locator_drift(tmp_path, field):
    path, declaration, indexes = _attempt(tmp_path)
    payload = json.loads(path.read_bytes())
    payload[field] = "0" * 64
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="bind|locator"):
        audit_direct_attempt(path, declaration, indexes, audited_at=AUDITED_AT)


def test_rejects_retained_rejection_body_claim(tmp_path):
    path, declaration, indexes = _attempt(tmp_path)
    payload = json.loads(path.read_bytes())
    payload["response_bytes_retained"] = True
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="byte disposition"):
        audit_direct_attempt(path, declaration, indexes, audited_at=AUDITED_AT)


def test_rejects_clock_reversal_and_duplicate_fields(tmp_path):
    path, declaration, indexes = _attempt(tmp_path)
    payload = json.loads(path.read_bytes())
    payload["completed_at_utc"] = "2026-09-13T01:04:02Z"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="precedes"):
        audit_direct_attempt(path, declaration, indexes, audited_at=AUDITED_AT)
    path.write_text('{"schema":"a","schema":"b"}')
    with pytest.raises(ValueError, match="duplicate"):
        audit_direct_attempt(path, declaration, indexes, audited_at=AUDITED_AT)


@pytest.mark.parametrize("audit_time", [
    datetime(2026, 9, 13, 2),
    datetime(2026, 9, 13, 2, tzinfo=timezone(timedelta(hours=1))),
    "2026-09-13T02:00:00Z",
])
def test_rejects_noncanonical_audit_clock(tmp_path, audit_time):
    path, declaration, indexes = _attempt(tmp_path)
    with pytest.raises(ValueError, match="audited_at must use"):
        audit_direct_attempt(path, declaration, indexes, audited_at=audit_time)


def test_receipt_must_exist_by_audit_time(tmp_path):
    path, declaration, indexes = _attempt(tmp_path)
    completed = datetime.fromisoformat(json.loads(path.read_bytes())["completed_at_utc"])
    with pytest.raises(ValueError, match="after audit time"):
        audit_direct_attempt(
            path, declaration, indexes,
            audited_at=completed - timedelta(microseconds=1),
        )
    result = audit_direct_attempt(path, declaration, indexes, audited_at=completed)
    assert result["audited_at"] == completed.isoformat()


def test_reported_hash_binds_exact_validated_bytes(tmp_path, monkeypatch):
    path, declaration, indexes = _attempt(tmp_path)
    original_read = Path.read_bytes
    original = original_read(path)
    reads = []

    def changing_read(self):
        if self == path:
            reads.append(self)
            return original if len(reads) == 1 else b"substituted after validation"
        return original_read(self)

    monkeypatch.setattr(Path, "read_bytes", changing_read)
    result = audit_direct_attempt(path, declaration, indexes, audited_at=AUDITED_AT)
    assert result["attempt_sha256"] == hashlib.sha256(original).hexdigest()
    assert len(reads) == 1
