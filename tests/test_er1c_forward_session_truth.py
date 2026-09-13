import hashlib
import json

import pytest

from tools.audit_er1c_forward_session_truth import audit_forward_session_truth


def _write_package(root):
    artifacts = {
        "nyse_2026_calendar.pdf": b"%PDF-1.7 /Type/Page fixture %%EOF",
        "nyse_hours_calendars.html": b"""<html>
        All NYSE markets observe U.S. holidays as listed below for 2026, 2027, and 2028.
        Labor Day Monday, September 7
        All times are Eastern Time.
        Core Trading Session: 9:30 a.m. to 4:00 p.m. ET
        </html>""",
    }
    locators = {
        "nyse_2026_calendar.pdf": "https://www.nyse.com/publicdocs/nyse/ICE_NYSE_2026_Yearly_Trading_Calendar.pdf",
        "nyse_hours_calendars.html": "https://www.nyse.com/trade/hours-calendars",
    }
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
    manifest = {"schema": "er1c-forward-session-truth-v1", "purpose": "fixture", "records": records}
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")


@pytest.fixture
def package(tmp_path):
    _write_package(tmp_path)
    return tmp_path


def _rewrite_manifest(root, mutate):
    path = root / "manifest.json"
    manifest = json.loads(path.read_bytes())
    mutate(manifest)
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")


def test_qualifies_current_schedule_without_overstating_us2_or_shadow_readiness(package):
    result = audit_forward_session_truth(package)
    assert result["requested_first_us_shadow_date"] == "2026-09-14"
    assert result["official_current_schedule_corroborated"] is True
    assert result["canonical_us2_session_evidence"] == "NO_GO"
    assert result["shadow_scoring"] == "NOT_READY"
    assert result["watchlist_frozen_by_cutoff"] is False


def test_rejects_tampered_artifact(package):
    (package / "nyse_hours_calendars.html").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity mismatch"):
        audit_forward_session_truth(package)


def test_rejects_historical_availability_overclaim(package):
    _rewrite_manifest(package, lambda doc: doc["records"][0].update(historical_availability_proven=True))
    with pytest.raises(ValueError, match="historical availability"):
        audit_forward_session_truth(package)


def test_rejects_missing_schedule_scope(package):
    path = package / "nyse_hours_calendars.html"
    payload = path.read_bytes().replace(b"All times are Eastern Time.", b"")
    path.write_bytes(payload)
    def update(doc):
        record = next(row for row in doc["records"] if row["filename"] == path.name)
        record.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    _rewrite_manifest(package, update)
    with pytest.raises(ValueError, match="schedule anchors missing"):
        audit_forward_session_truth(package)


def test_rejects_undeclared_inventory(package):
    (package / "extra").write_text("extra")
    with pytest.raises(ValueError, match="inventory"):
        audit_forward_session_truth(package)
