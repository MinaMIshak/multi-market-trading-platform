import hashlib
import json
from datetime import datetime, timezone

import pytest

from tools.audit_er1c_nyse_corporate_actions_product import (
    PRODUCT_SOURCE,
    PURPOSE,
    SPEC_SOURCE,
    audit_product,
)


AUDITED_AT = datetime(2026, 9, 13, 6, tzinfo=timezone.utc)
PAGE = b"""<html><body>
Market Event Feed is a real-time API that provides programmatic access to Corporate Actions
over 60 different corporate actions types for all equities listed on NYSE Group
cash dividends, stock dividends, distributions, splits, new listings (IPOs), suspensions and delistings
consolidated view of daily corporate events happening on the NYSE Group
on the current Trading Day
</body></html>"""
PDF = b"%PDF-1.7\nfixture only\n%%EOF\n"


def _artifact(filename, source, payload, started="2026-09-13T05:39:00Z"):
    return {
        "bytes": len(payload), "filename": filename,
        "historical_availability_proven": False, "http_status": 200,
        "resolved_locator": source, "retrieval_completed_at_utc": "2026-09-13T05:40:00Z",
        "retrieval_started_at_utc": started, "sha256": hashlib.sha256(payload).hexdigest(),
        "source_locator": source,
    }


def _write(root):
    (root / "nyse_corporate_actions_client_spec_v2.2.6.pdf").write_bytes(PDF)
    (root / "nyse_corporate_actions_product.html").write_bytes(PAGE)
    manifest = _artifact("nyse_corporate_actions_client_spec_v2.2.6.pdf", SPEC_SOURCE, PDF)
    manifest.update({
        "product_page": _artifact("nyse_corporate_actions_product.html", PRODUCT_SOURCE, PAGE),
        "purpose": PURPOSE, "schema": "er1c-nyse-corporate-actions-client-spec-v1",
    })
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")


@pytest.fixture
def package(tmp_path):
    _write(tmp_path)
    return tmp_path


def _mutate(root, mutation):
    manifest = json.loads((root / "manifest.json").read_bytes())
    mutation(manifest)
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")


def test_qualifies_documentation_without_promoting_event_coverage(package):
    result = audit_product(package, audited_at=AUDITED_AT)
    assert result["documented_event_scope"] == "OVER_60_TYPES_ACROSS_NYSE_GROUP"
    assert result["event_files_acquired"] is False
    assert result["historical_archive_access_proven_free"] is False
    assert result["complete_bounded_us4_action_coverage"] == "NO_GO"
    assert result["canonical_pit_admission"] == "NO_GO"


@pytest.mark.parametrize("filename", [
    "nyse_corporate_actions_client_spec_v2.2.6.pdf",
    "nyse_corporate_actions_product.html",
])
def test_rejects_tampered_artifact(package, filename):
    (package / filename).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity mismatch"):
        audit_product(package, audited_at=AUDITED_AT)


def test_rejects_historical_availability_overclaim(package):
    _mutate(package, lambda doc: doc["product_page"].update(historical_availability_proven=True))
    with pytest.raises(ValueError, match="historical availability"):
        audit_product(package, audited_at=AUDITED_AT)


def test_rejects_repurposed_manifest(package):
    _mutate(package, lambda doc: doc.update(purpose="complete 2022 actions"))
    with pytest.raises(ValueError, match="evidence purpose"):
        audit_product(package, audited_at=AUDITED_AT)


def test_rejects_missing_scope_anchor(package):
    payload = PAGE.replace(b"over 60 different", b"many")
    (package / "nyse_corporate_actions_product.html").write_bytes(payload)
    _mutate(package, lambda doc: doc["product_page"].update(
        bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()))
    with pytest.raises(ValueError, match="scope anchors missing"):
        audit_product(package, audited_at=AUDITED_AT)


def test_rejects_future_receipt(package):
    _mutate(package, lambda doc: doc.update(retrieval_completed_at_utc="2026-09-13T06:01:00Z"))
    with pytest.raises(ValueError, match="after audit time"):
        audit_product(package, audited_at=AUDITED_AT)


def test_rejects_undeclared_inventory(package):
    (package / "event.csv").write_text("not acquired")
    with pytest.raises(ValueError, match="inventory"):
        audit_product(package, audited_at=AUDITED_AT)
