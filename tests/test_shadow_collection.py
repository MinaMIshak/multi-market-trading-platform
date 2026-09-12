"""Offline operational collector fixtures; no market facts or recommendations."""
import json
from datetime import timedelta

import pytest

from app.paper import shadow_collection, shadow_freeze
from app.paper.shadow_collection import complete_watchlist, record_missed_session
from app.research.historical_evidence import (
    HistoricalAttachmentReference, HistoricalAvailability,
    HistoricalAvailabilityEvidence, HistoricalEvidenceAttachment,
    HistoricalEvidencePackage, HistoricalRawReceipt, HistoricalSourceReview,
)
from tests.test_shadow_records import AT, watchlist


def package(identity_char, reference, covered_fields=("fixture",)):
    raw = HistoricalRawReceipt(
        provider="fixture", source="fixture", source_locator=reference.source_locator,
        sha256=reference.artifact_sha256, byte_size=10,
        local_received_at=AT - timedelta(days=1), source_edition="fixture-v1",
        evidence_category="SHADOW_INPUT_FIXTURE",
    )
    attachment = HistoricalEvidenceAttachment(
        source_locator=f"fixture://review/{identity_char}", sha256=identity_char * 64,
        local_received_at=AT - timedelta(days=1), description="fixture proof",
        source_authority_context=reference.source_authority,
    )
    attachment_ref = HistoricalAttachmentReference(
        attachment_id=attachment.identity, sha256=attachment.sha256,
    )
    evidence = HistoricalAvailabilityEvidence(
        subject_receipt_id=raw.identity, subject_sha256=raw.sha256,
        source_edition="fixture-v1", covered_scope="fixture shadow input",
        covered_fields=covered_fields, revision_semantics="immutable fixture",
        attachments=(attachment_ref,),
        availability=HistoricalAvailability(kind="EXACT", exact_at=reference.available_at),
    )
    review = HistoricalSourceReview(
        reviewer="fixture", reviewed_at=AT - timedelta(hours=1, minutes=1),
        methodology="fixture review", approved=True, subject_receipt_id=raw.identity,
        subject_sha256=raw.sha256, availability_evidence_id=evidence.identity,
        attachments=(attachment_ref,),
    )
    result = HistoricalEvidencePackage(
        raw_receipt=raw, evidence=evidence, review=review, attachments=(attachment,),
    )
    return result


def authenticated_watchlist():
    preliminary = watchlist()
    packages = tuple(package(char, reference) for char, reference in zip("ab", preliminary.evidence))
    evidence = tuple(
        reference.model_copy(update={"evidence_id": item.identity})
        for reference, item in zip(preliminary.evidence, packages)
    )
    session = preliminary.session.model_copy(update={"evidence_ids": (packages[0].identity,)})
    candidate = preliminary.candidates[0].model_copy(update={"evidence_ids": (packages[1].identity,)})
    return preliminary.model_copy(update={"evidence": evidence, "session": session, "candidates": (candidate,)}), packages


def test_authenticated_watchlist_gets_separate_completion_receipt(tmp_path, monkeypatch):
    item, packages = authenticated_watchlist()
    monkeypatch.setattr(shadow_collection, "_now", lambda: AT)
    monkeypatch.setattr(shadow_freeze, "_now", lambda: AT)
    path = complete_watchlist(tmp_path, item, packages)
    receipt = json.loads(path.read_bytes())
    assert receipt["status"] == "FROZEN"
    assert receipt["scoring"] == "NOT SCORED"
    assert receipt["evidence_package_ids"] == sorted(item.identity for item in packages)
    assert (tmp_path / "watchlists" / f"{item.record_id}.json").exists()


def test_collector_rejects_unbound_or_unapproved_evidence(tmp_path, monkeypatch):
    item, packages = authenticated_watchlist()
    monkeypatch.setattr(shadow_collection, "_now", lambda: AT)
    monkeypatch.setattr(shadow_freeze, "_now", lambda: AT)
    with pytest.raises(ValueError, match="match watchlist"):
        complete_watchlist(tmp_path, item, packages[:1])
    bad = packages[0].model_copy(update={
        "review": packages[0].review.model_copy(update={"approved": False})
    })
    with pytest.raises(ValueError, match="approved review"):
        complete_watchlist(tmp_path, item, (bad, packages[1]))
    assert not list(tmp_path.iterdir())


def test_missed_session_is_only_recorded_after_cutoff(tmp_path, monkeypatch):
    item, packages = authenticated_watchlist()
    monkeypatch.setattr(shadow_collection, "_now", lambda: AT)
    with pytest.raises(ValueError, match="before decision cutoff"):
        record_missed_session(
            tmp_path, record_id=item.record_id, session=item.session,
            session_package=packages[0], reason="NO_TIMELY_WATCHLIST",
        )
    monkeypatch.setattr(
        shadow_collection, "_now", lambda: item.session.decision_cutoff,
    )
    path = record_missed_session(
        tmp_path, record_id=item.record_id, session=item.session,
        session_package=packages[0], reason="NO_TIMELY_WATCHLIST",
    )
    receipt = json.loads(path.read_bytes())
    assert receipt["status"] == "MISSED"
    assert receipt["scoring"] == "NOT SCORED"


def test_existing_watchlist_cannot_be_relabelled_missed(tmp_path, monkeypatch):
    item, packages = authenticated_watchlist()
    monkeypatch.setattr(shadow_collection, "_now", lambda: AT)
    monkeypatch.setattr(shadow_freeze, "_now", lambda: AT)
    complete_watchlist(tmp_path, item, packages)
    monkeypatch.setattr(shadow_collection, "_now", lambda: item.session.decision_cutoff)
    with pytest.raises(ValueError, match="watchlist exists"):
        record_missed_session(
            tmp_path, record_id=item.record_id, session=item.session,
            session_package=packages[0], reason="COLLECTION_FAILED",
        )


def test_missed_session_rejects_unsafe_record_identity(tmp_path, monkeypatch):
    item, packages = authenticated_watchlist()
    monkeypatch.setattr(shadow_collection, "_now", lambda: item.session.decision_cutoff)
    with pytest.raises(ValueError, match="invalid record_id"):
        record_missed_session(
            tmp_path, record_id="../escape", session=item.session,
            session_package=packages[0], reason="NO_TIMELY_WATCHLIST",
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("offset", [-1, 1])
def test_reference_cannot_restate_package_availability(tmp_path, monkeypatch, offset):
    item, packages = authenticated_watchlist()
    monkeypatch.setattr(shadow_collection, "_now", lambda: AT)
    reference = item.evidence[0]
    changed = reference.model_copy(update={
        "available_at": reference.available_at + timedelta(seconds=offset),
    })
    item = item.model_copy(update={"evidence": (changed, item.evidence[1])})
    with pytest.raises(ValueError, match="package latest availability"):
        complete_watchlist(tmp_path, item, packages)
    assert not list(tmp_path.iterdir())


def test_bounded_availability_requires_conservative_endpoint(tmp_path, monkeypatch):
    item, packages = authenticated_watchlist()
    original = packages[0]
    end = item.evidence[0].available_at
    evidence = original.evidence.model_copy(update={
        "availability": HistoricalAvailability(
            kind="BOUNDED_INTERVAL", start=end - timedelta(hours=1), end=end,
        ),
    })
    review = original.review.model_copy(update={"availability_evidence_id": evidence.identity})
    bounded = original.model_copy(update={"evidence": evidence, "review": review})
    reference = item.evidence[0].model_copy(update={"evidence_id": bounded.identity})
    item = item.model_copy(update={
        "evidence": (reference, item.evidence[1]),
        "session": item.session.model_copy(update={"evidence_ids": (bounded.identity,)}),
    })
    monkeypatch.setattr(shadow_collection, "_now", lambda: AT)
    monkeypatch.setattr(shadow_freeze, "_now", lambda: AT)
    early = item.model_copy(update={"evidence": (
        reference.model_copy(update={"available_at": end - timedelta(hours=1)}),
        item.evidence[1],
    )})
    with pytest.raises(ValueError, match="package latest availability"):
        complete_watchlist(tmp_path, early, (bounded, packages[1]))
    assert not list(tmp_path.iterdir())
    assert complete_watchlist(tmp_path, item, (bounded, packages[1])).exists()


@pytest.mark.parametrize("reason", ["VERIFIED", "", None])
def test_missed_session_rejects_unsupported_reason(tmp_path, monkeypatch, reason):
    item, packages = authenticated_watchlist()
    monkeypatch.setattr(shadow_collection, "_now", lambda: item.session.decision_cutoff)
    with pytest.raises(ValueError, match="unsupported missed-session reason"):
        record_missed_session(
            tmp_path, record_id=item.record_id, session=item.session,
            session_package=packages[0], reason=reason,
        )
    assert not list(tmp_path.iterdir())


def test_collector_revalidates_copied_watchlist_before_io(tmp_path, monkeypatch):
    item, packages = authenticated_watchlist()
    item = item.model_copy(update={"label": "LIVE READY"})
    monkeypatch.setattr(shadow_collection, "_now", lambda: AT)
    with pytest.raises(ValueError):
        complete_watchlist(tmp_path, item, packages)
    assert not list(tmp_path.iterdir())


def completed_fixture(tmp_path, monkeypatch):
    item, packages = authenticated_watchlist()
    monkeypatch.setattr(shadow_collection, "_now", lambda: AT)
    monkeypatch.setattr(shadow_freeze, "_now", lambda: AT)
    path = complete_watchlist(tmp_path, item, packages)
    return item, packages, path


def test_audit_completion_remains_unscored_and_read_only(tmp_path, monkeypatch):
    item, packages, path = completed_fixture(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    receipt = shadow_collection.audit_completed_watchlist(tmp_path, item, packages)
    assert receipt == json.loads(path.read_bytes())
    assert receipt["scoring"] == "NOT SCORED"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}


@pytest.mark.parametrize("field,value", [
    ("status", "MISSED"), ("scoring", "SCORED"), ("label", "LIVE READY"),
    ("record_id", "another"), ("market", "EGX"), ("market_date", "2000-01-01"),
    ("watchlist_sha256", "0" * 64), ("document_sha256", "0" * 64),
    ("evidence_package_ids", []), ("decision_cutoff", AT.isoformat()),
    ("completed_at", (AT - timedelta(seconds=1)).isoformat()),
    ("completed_at", (AT + timedelta(days=1)).isoformat()),
    ("extra", True),
])
def test_audit_rejects_receipt_tampering(tmp_path, monkeypatch, field, value):
    item, packages, path = completed_fixture(tmp_path, monkeypatch)
    receipt = json.loads(path.read_bytes())
    receipt[field] = value
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        shadow_collection.audit_completed_watchlist(tmp_path, item, packages)


def test_audit_requires_completion_and_exact_document(tmp_path, monkeypatch):
    item, packages, path = completed_fixture(tmp_path, monkeypatch)
    altered = item.model_copy(update={"candidates": (
        item.candidates[0].model_copy(update={"thesis": "changed thesis"}),
    )})
    with pytest.raises(ValueError, match="does not bind watchlist"):
        shadow_collection.audit_completed_watchlist(tmp_path, altered, packages)
    with pytest.raises(ValueError, match="match watchlist"):
        shadow_collection.audit_completed_watchlist(tmp_path, item, packages[:1])
    path.unlink()
    with pytest.raises(FileNotFoundError):
        shadow_collection.audit_completed_watchlist(tmp_path, item, packages)


def test_audit_rejects_duplicate_completion_keys(tmp_path, monkeypatch):
    item, packages, path = completed_fixture(tmp_path, monkeypatch)
    original = path.read_text()
    path.write_text('{"status":"MISSED",' + original[1:])
    with pytest.raises(ValueError, match="duplicate completion field"):
        shadow_collection.audit_completed_watchlist(tmp_path, item, packages)


def test_audit_rechecks_evidence_at_freeze_time(tmp_path, monkeypatch):
    item, packages, path = completed_fixture(tmp_path, monkeypatch)
    calls = []
    original = shadow_collection._admit_packages

    def capture(watchlist, evidence_packages, *, built_at):
        calls.append(built_at)
        return original(watchlist, evidence_packages, built_at=built_at)

    monkeypatch.setattr(shadow_collection, "_now", lambda: AT + timedelta(days=10))
    monkeypatch.setattr(shadow_collection, "_admit_packages", capture)
    shadow_collection.audit_completed_watchlist(tmp_path, item, packages)
    assert calls == [AT]
