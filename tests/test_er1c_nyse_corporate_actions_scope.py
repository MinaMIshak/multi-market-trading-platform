import hashlib
import json
from datetime import datetime, timezone

import pytest

from tools.audit_er1c_nyse_corporate_actions_scope import PURPOSE, SOURCE, audit_scope


AUDITED_AT = datetime(2026, 9, 13, 6, tzinfo=timezone.utc)
PAGE = b"""<html><body>
Advance notice to the Exchange and public dissemination via a press release
change in name/symbol/CUSIP, reverse split, redomestication, business reorganization
at least ten calendar days in advance of the effective date of the corporate action
The Exchange publishes upcoming corporate action events on a daily basis.
issuer name and symbol, anticipated date, corporate action type, and status
info notices, which are available through a Market Data subscription
</body></html>"""


def _write(root, payload=PAGE):
    (root / "nyse_corporate_actions.html").write_bytes(payload)
    manifest = {
        "bytes": len(payload),
        "filename": "nyse_corporate_actions.html",
        "historical_availability_proven": False,
        "http_status": 200,
        "purpose": PURPOSE,
        "resolved_locator": SOURCE,
        "retrieval_completed_at_utc": "2026-09-13T05:36:00Z",
        "retrieval_started_at_utc": "2026-09-13T05:35:00Z",
        "schema": "er1c-nyse-corporate-actions-scope-v1",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "source_locator": SOURCE,
    }
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")


@pytest.fixture
def package(tmp_path):
    _write(tmp_path)
    return tmp_path


def _mutate_manifest(root, mutate):
    manifest = json.loads((root / "manifest.json").read_bytes())
    mutate(manifest)
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")


def test_qualifies_policy_scope_without_promoting_action_coverage(package):
    result = audit_scope(package, audited_at=AUDITED_AT)
    assert result["current_list_scope"] == "UPCOMING_EVENTS_DAILY"
    assert result["event_specific_info_notice_access"] == "MARKET_DATA_SUBSCRIPTION"
    assert result["historical_availability"] == "UNPROVEN"
    assert result["complete_bounded_us4_action_coverage"] == "NO_GO"
    assert result["canonical_pit_admission"] == "NO_GO"


def test_rejects_tampered_artifact(package):
    (package / "nyse_corporate_actions.html").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity mismatch"):
        audit_scope(package, audited_at=AUDITED_AT)


def test_rejects_historical_availability_overclaim(package):
    _mutate_manifest(package, lambda doc: doc.update(historical_availability_proven=True))
    with pytest.raises(ValueError, match="historical availability"):
        audit_scope(package, audited_at=AUDITED_AT)


def test_rejects_repurposed_manifest(package):
    _mutate_manifest(package, lambda doc: doc.update(purpose="complete 2022 actions"))
    with pytest.raises(ValueError, match="evidence purpose"):
        audit_scope(package, audited_at=AUDITED_AT)


def test_rejects_missing_publication_scope_anchor(package):
    payload = PAGE.replace(b"upcoming corporate action events", b"corporate action events")
    _write(package, payload)
    with pytest.raises(ValueError, match="scope anchors missing"):
        audit_scope(package, audited_at=AUDITED_AT)


def test_rejects_future_dated_receipt(package):
    _mutate_manifest(
        package,
        lambda doc: doc.update(retrieval_completed_at_utc="2026-09-13T06:01:00Z"),
    )
    with pytest.raises(ValueError, match="after audit time"):
        audit_scope(package, audited_at=AUDITED_AT)


def test_rejects_undeclared_inventory(package):
    (package / "extra.html").write_text("undeclared")
    with pytest.raises(ValueError, match="inventory"):
        audit_scope(package, audited_at=AUDITED_AT)
