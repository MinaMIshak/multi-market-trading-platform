import hashlib
import json
from datetime import datetime, timezone

import pytest

from tools import audit_er1c_nyse_symbol_mapping_spec as subject

AUDITED_AT = datetime(2026, 9, 13, 7, tzinfo=timezone.utc)


@pytest.fixture
def package(tmp_path, monkeypatch):
    payloads = {
        "nyse_bqt_common_client_spec_v2.2l.pdf": b"%PDF-1.7 specification",
        "nyse_symbol_mapping_filename_change_march2022.pdf": b"%PDF-1.7 notice",
    }
    locators = {name: values[0] for name, values in subject.EXPECTED.items()}
    expected, records = {}, []
    for name, payload in payloads.items():
        digest = hashlib.sha256(payload).hexdigest()
        expected[name] = (locators[name], len(payload), digest)
        (tmp_path / name).write_bytes(payload)
        records.append({
            "filename": name, "source_locator": locators[name],
            "resolved_locator": locators[name], "http_status": 200,
            "content_type": "application/pdf", "http_last_modified": None,
            "receipt_started_utc": "2026-09-13T06:27:54+00:00",
            "receipt_completed_utc": "2026-09-13T06:27:55+00:00",
            "bytes": len(payload), "sha256": digest,
            "historical_availability_proven": False,
        })
    monkeypatch.setattr(subject, "EXPECTED", expected)
    manifest = {"schema": "er1c-nyse-symbol-mapping-spec-v1", "purpose": subject.PURPOSE, "records": records}
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (tmp_path / "manifest.json").write_bytes(raw)
    (tmp_path / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")
    return tmp_path


def _rewrite(root, mutate):
    manifest = json.loads((root / "manifest.json").read_bytes())
    mutate(manifest)
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")


def test_documents_pass_but_identity_and_universe_fail_closed(package):
    result = subject.audit_symbol_mapping_spec(package, audited_at=AUDITED_AT)
    assert result["documentation_integrity"] == "PASS"
    assert result["market_and_listing_fields_documented"] is True
    assert result["common_stock_field_documented_in_reviewed_layout"] is False
    assert result["mapping_data_requests_made"] == 0
    assert result["canonical_us1_identity"] == result["canonical_us5a_universe"] == "NO_GO"


def test_rejects_tampered_document(package):
    (package / "nyse_bqt_common_client_spec_v2.2l.pdf").write_bytes(b"%PDF-tampered")
    with pytest.raises(ValueError, match="integrity mismatch"):
        subject.audit_symbol_mapping_spec(package, audited_at=AUDITED_AT)


def test_rejects_rehashed_substituted_edition(package):
    name, payload = "nyse_bqt_common_client_spec_v2.2l.pdf", b"%PDF-1.7 substituted"
    (package / name).write_bytes(payload)
    def mutate(doc):
        row = next(row for row in doc["records"] if row["filename"] == name)
        row.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    _rewrite(package, mutate)
    with pytest.raises(ValueError, match="reviewed edition mismatch"):
        subject.audit_symbol_mapping_spec(package, audited_at=AUDITED_AT)


def test_rejects_redirect_or_historical_availability_overclaim(package):
    _rewrite(package, lambda doc: doc["records"][0].update(resolved_locator="https://example.test/"))
    with pytest.raises(ValueError, match="redirect"):
        subject.audit_symbol_mapping_spec(package, audited_at=AUDITED_AT)
    _rewrite(package, lambda doc: doc["records"][0].update(
        resolved_locator=doc["records"][0]["source_locator"], historical_availability_proven=True,
    ))
    with pytest.raises(ValueError, match="historical-availability"):
        subject.audit_symbol_mapping_spec(package, audited_at=AUDITED_AT)


def test_rejects_extra_inventory_and_naive_receipt(package):
    (package / "NYSESymbolMapping_20220701.txt").write_text("not acquired")
    with pytest.raises(ValueError, match="inventory"):
        subject.audit_symbol_mapping_spec(package, audited_at=AUDITED_AT)
    (package / "NYSESymbolMapping_20220701.txt").unlink()
    _rewrite(package, lambda doc: doc["records"][0].update(receipt_completed_utc="2026-09-13T06:27:55"))
    with pytest.raises(ValueError, match="explicit UTC"):
        subject.audit_symbol_mapping_spec(package, audited_at=AUDITED_AT)
