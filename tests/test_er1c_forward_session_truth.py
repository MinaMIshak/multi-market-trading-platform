import hashlib
import json
from datetime import datetime, timezone

import pytest

from tools import audit_er1c_forward_session_truth as subject


AUDITED_AT = datetime(2026, 9, 13, 1, tzinfo=timezone.utc)


def _write_package(root, monkeypatch):
    artifacts = {
        "nyse_2026_calendar.pdf": b"%PDF-1.7 /Type/Page fixture %%EOF",
        "nyse_hours_calendars.html": b"""<html>
        All NYSE markets observe U.S. holidays as listed below for 2026, 2027, and 2028.
        Labor Day Monday, September 7
        All times are Eastern Time.
        Core Trading Session: 9:30 a.m. to 4:00 p.m. ET
        </html>""",
    }
    locators = {name: values[0] for name, values in subject.EXPECTED.items()}
    monkeypatch.setattr(subject, "EXPECTED", {
        name: (locators[name], len(payload), hashlib.sha256(payload).hexdigest())
        for name, payload in artifacts.items()
    })
    records = []
    for name, payload in artifacts.items():
        (root / name).write_bytes(payload)
        records.append({
            "bytes": len(payload), "filename": name,
            "historical_availability_proven": False, "http_status": 200,
            "retrieval_completed_at_utc": "2026-09-13T00:01:00Z",
            "retrieval_started_at_utc": "2026-09-13T00:00:00Z",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "source_locator": locators[name],
        })
    manifest = {
        "schema": "er1c-forward-session-truth-v1",
        "purpose": (
            "Bounded official-source qualification for requested first US shadow dates; "
            "not canonical US2 admission or a watchlist."
        ),
        "records": records,
    }
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")


@pytest.fixture
def package(tmp_path, monkeypatch):
    _write_package(tmp_path, monkeypatch)
    return tmp_path


def _rewrite_manifest(root, mutate):
    path = root / "manifest.json"
    manifest = json.loads(path.read_bytes())
    mutate(manifest)
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")


def _requalify_fixture_edition(monkeypatch, name, payload):
    expected = dict(subject.EXPECTED)
    expected[name] = (expected[name][0], len(payload), hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(subject, "EXPECTED", expected)


def test_qualifies_current_schedule_without_overstating_us2_or_shadow_readiness(package):
    result = subject.audit_forward_session_truth(package, audited_at=AUDITED_AT)
    assert result["requested_first_us_shadow_date"] == "2026-09-14"
    assert result["official_schedule_text_anchors_present"] is True
    assert result["target_date_session_status"] == "UNKNOWN"
    assert result["canonical_us2_session_evidence"] == "NO_GO"
    assert result["shadow_scoring"] == "NOT_READY"
    assert result["watchlist_frozen_by_cutoff"] == "UNKNOWN"
    assert result["latest_receipt_at"] == "2026-09-13T00:01:00+00:00"


def test_rejects_tampered_artifact(package):
    (package / "nyse_hours_calendars.html").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity mismatch"):
        subject.audit_forward_session_truth(package, audited_at=AUDITED_AT)


def test_rejects_historical_availability_overclaim(package):
    _rewrite_manifest(package, lambda doc: doc["records"][0].update(historical_availability_proven=True))
    with pytest.raises(ValueError, match="historical availability"):
        subject.audit_forward_session_truth(package, audited_at=AUDITED_AT)


def test_rejects_rehashed_substituted_edition(package):
    path = package / "nyse_2026_calendar.pdf"
    payload = b"%PDF-1.7 /Type/Page substituted %%EOF"
    path.write_bytes(payload)

    def update(doc):
        record = next(row for row in doc["records"] if row["filename"] == path.name)
        record.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())

    _rewrite_manifest(package, update)
    with pytest.raises(ValueError, match="reviewed edition mismatch"):
        subject.audit_forward_session_truth(package, audited_at=AUDITED_AT)


def test_rejects_missing_schedule_scope(package, monkeypatch):
    path = package / "nyse_hours_calendars.html"
    payload = path.read_bytes().replace(b"All times are Eastern Time.", b"")
    path.write_bytes(payload)
    _requalify_fixture_edition(monkeypatch, path.name, payload)
    def update(doc):
        record = next(row for row in doc["records"] if row["filename"] == path.name)
        record.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    _rewrite_manifest(package, update)
    with pytest.raises(ValueError, match="schedule anchors missing"):
        subject.audit_forward_session_truth(package, audited_at=AUDITED_AT)


def test_rejects_undeclared_inventory(package):
    (package / "extra").write_text("extra")
    with pytest.raises(ValueError, match="inventory"):
        subject.audit_forward_session_truth(package, audited_at=AUDITED_AT)


def test_rejects_future_dated_receipt(package):
    _rewrite_manifest(
        package,
        lambda doc: doc["records"][0].update(
            retrieval_started_at_utc="2026-09-13T01:01:00Z",
            retrieval_completed_at_utc="2026-09-13T01:01:00Z",
        ),
    )
    with pytest.raises(ValueError, match="after audit time"):
        subject.audit_forward_session_truth(package, audited_at=AUDITED_AT)


def test_rejects_repurposed_manifest(package):
    _rewrite_manifest(package, lambda doc: doc.update(purpose="canonical session proof"))
    with pytest.raises(ValueError, match="evidence purpose"):
        subject.audit_forward_session_truth(package, audited_at=AUDITED_AT)


@pytest.mark.parametrize("extra_text", [
    "",  # A partial page with anchors is not a complete exceptions calendar.
    "Special closure: September 14, 2026.",
    "Early close: September 14, 2026 at 1 p.m. ET.",
])
def test_general_anchors_never_establish_target_date_or_watchlist(package, extra_text, monkeypatch):
    path = package / "nyse_hours_calendars.html"
    payload = path.read_bytes().replace(b"</html>", extra_text.encode() + b"</html>")
    path.write_bytes(payload)
    _requalify_fixture_edition(monkeypatch, path.name, payload)

    def update(doc):
        record = next(row for row in doc["records"] if row["filename"] == path.name)
        record.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())

    _rewrite_manifest(package, update)
    result = subject.audit_forward_session_truth(package, audited_at=AUDITED_AT)
    assert result["target_date_session_status"] == "UNKNOWN"
    assert "has not been verified" in result["calendar_observation"]
    assert "is absent" not in result["calendar_observation"]
    assert result["watchlist_frozen_by_cutoff"] == "UNKNOWN"
    assert result["canonical_us2_session_evidence"] == "NO_GO"
    assert result["shadow_scoring"] == "NOT_READY"
