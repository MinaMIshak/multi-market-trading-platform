import hashlib
import json
from datetime import datetime, timezone

import pytest

from tools import audit_er1c_nasdaq_halt_terms as subject


AUDITED_AT = datetime(2026, 9, 13, 7, tzinfo=timezone.utc)


@pytest.fixture
def package(tmp_path, monkeypatch):
    payloads = {
        "rss_documentation.html": b"<html>documentation</html>",
        "rss_terms.pdf": b"%PDF-1.5 reviewed terms",
        "rss_queries.html": b"<html>queries</html>",
    }
    expected = {}
    records = []
    locators = {name: values[0] for name, values in subject.EXPECTED.items()}
    for name, payload in payloads.items():
        digest = hashlib.sha256(payload).hexdigest()
        expected[name] = (locators[name], len(payload), digest)
        (tmp_path / name).write_bytes(payload)
        records.append({
            "filename": name,
            "source_locator": locators[name],
            "resolved_locator": locators[name],
            "http_status": 200,
            "bytes": len(payload),
            "sha256": digest,
            "retrieval_started_at_utc": "2026-09-13T06:00:00+00:00",
            "retrieval_completed_at_utc": "2026-09-13T06:01:00+00:00",
            "historical_availability_proven": False,
        })
    monkeypatch.setattr(subject, "EXPECTED", expected)
    manifest = {
        "schema": "er1c-nasdaq-halt-qualification-v1",
        "purpose": subject.PURPOSE,
        "records": records,
    }
    raw = (json.dumps(manifest, indent=2) + "\n").encode()
    (tmp_path / "manifest.json").write_bytes(raw)
    (tmp_path / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")
    return tmp_path


def _rewrite_manifest(root, mutate):
    manifest = json.loads((root / "manifest.json").read_bytes())
    mutate(manifest)
    raw = (json.dumps(manifest, indent=2) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")


def test_terms_pass_integrity_but_feed_use_fails_closed(package):
    result = subject.audit_halt_terms(package, audited_at=AUDITED_AT)
    assert result["documentation_integrity"] == "PASS"
    assert result["feed_requests_made"] == 0
    assert result["raw_retention_permission"] == "UNRESOLVED"
    assert result["transformation_permission"] == "UNRESOLVED"
    assert result["feed_acquisition"] == result["canonical_admission"] == "NO_GO"


def test_rejects_artifact_tamper(package):
    (package / "rss_terms.pdf").write_bytes(b"%PDF-tampered")
    with pytest.raises(ValueError, match="integrity mismatch"):
        subject.audit_halt_terms(package, audited_at=AUDITED_AT)


def test_rejects_substituted_terms_edition_even_if_manifest_matches(package):
    payload = b"%PDF-1.5 different edition"
    (package / "rss_terms.pdf").write_bytes(payload)
    def substitute(doc):
        row = next(row for row in doc["records"] if row["filename"] == "rss_terms.pdf")
        row.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())

    _rewrite_manifest(package, substitute)
    with pytest.raises(ValueError, match="reviewed edition mismatch"):
        subject.audit_halt_terms(package, audited_at=AUDITED_AT)


def test_rejects_redirect_and_false_historical_claim(package):
    _rewrite_manifest(
        package,
        lambda doc: doc["records"][0].update(resolved_locator="https://example.test/"),
    )
    with pytest.raises(ValueError, match="redirect"):
        subject.audit_halt_terms(package, audited_at=AUDITED_AT)

    def claim_historical_availability(doc):
        doc["records"][0].update(
            resolved_locator=doc["records"][0]["source_locator"],
            historical_availability_proven=True,
        )

    _rewrite_manifest(package, claim_historical_availability)
    with pytest.raises(ValueError, match="historical-availability"):
        subject.audit_halt_terms(package, audited_at=AUDITED_AT)


def test_rejects_unbounded_inventory_and_non_utc_receipt(package):
    (package / "feed.xml").write_text("not acquired")
    with pytest.raises(ValueError, match="inventory"):
        subject.audit_halt_terms(package, audited_at=AUDITED_AT)
    (package / "feed.xml").unlink()
    _rewrite_manifest(
        package,
        lambda doc: doc["records"][0].update(
            retrieval_completed_at_utc="2026-09-13T06:01:00"
        ),
    )
    with pytest.raises(ValueError, match="explicit UTC"):
        subject.audit_halt_terms(package, audited_at=AUDITED_AT)
