"""Artificial forward-fact fixtures only; no market facts, triggers or fills."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.paper import shadow_collection, shadow_facts, shadow_freeze, shadow_ledger
from app.paper.shadow_facts import (
    ACTION_FIELDS, BAR_FIELDS, IDENTITY_FIELDS, SESSION_FIELDS, ForwardActionCoverage,
    ForwardFactBundle, ForwardIdentityFact, ForwardRawBar, ForwardSessionFact,
)
from app.paper.shadow_records import ShadowEvidenceReference
from tests.test_shadow_collection import authenticated_watchlist, package


def prepared(tmp_path, monkeypatch):
    item, watchlist_packages = authenticated_watchlist()
    completed_at = item.generated_at + timedelta(minutes=30)
    monkeypatch.setattr(shadow_collection, "_now", lambda: completed_at)
    monkeypatch.setattr(shadow_freeze, "_now", lambda: completed_at)
    shadow_collection.complete_watchlist(tmp_path, item, watchlist_packages)
    monkeypatch.setattr(shadow_ledger, "_now", lambda: completed_at)
    shadow_ledger.append_candidate_event(tmp_path, item, watchlist_packages)

    start = item.session.opens_at
    available = start + timedelta(minutes=6)
    reference = ShadowEvidenceReference(
        evidence_id="c" * 64, source_authority="official fixture authority",
        source_locator="fixture://forward-bars", artifact_sha256="c" * 64,
        available_at=available,
    )
    fact_package = package("c", reference, tuple(
        SESSION_FIELDS | IDENTITY_FIELDS | ACTION_FIELDS | BAR_FIELDS
    ))
    facts = ForwardFactBundle(
        record_id=item.record_id, candidate_id=item.candidates[0].candidate_id,
        session=ForwardSessionFact(
            market="US", market_date=item.session.market_date, calendar_mic="XNYS",
            state="OPEN", opens_at=start, closes_at=start + timedelta(hours=6, minutes=30),
            evidence_package_id=fact_package.identity,
        ),
        identity=ForwardIdentityFact(
            instrument_id=item.candidates[0].instrument_id, ticker="IBM", listing_mic="XNYS",
            effective_from=item.session.market_date, effective_through=item.session.market_date,
            evidence_package_id=fact_package.identity,
        ),
        action_coverage=ForwardActionCoverage(
            instrument_id=item.candidates[0].instrument_id,
            coverage_from=item.session.market_date, coverage_through=item.session.market_date,
            status="COMPLETE", actions=(), evidence_package_id=fact_package.identity,
        ),
        bars=(ForwardRawBar(
            instrument_id=item.candidates[0].instrument_id, ticker="IBM",
            market_date=item.session.market_date, sequence=1, interval_start=start,
            interval_end=start + timedelta(minutes=5), available_at=available,
            is_final=True, price_basis="RAW_UNADJUSTED", open=Decimal("100"),
            high=Decimal("102"), low=Decimal("99"), close=Decimal("101"), volume=1000,
            source_row="fixture-row-1", evidence_package_id=fact_package.identity,
        ),),
    )
    return item, watchlist_packages, facts, (fact_package,), available


def test_admits_exact_opening_origin_facts_without_execution(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, available = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(shadow_facts, "_now", lambda: available + timedelta(minutes=1))
    path = shadow_facts.append_forward_fact_event(
        tmp_path, item, packages, facts, fact_packages,
    )
    event = json.loads(path.read_bytes())
    assert event["facts"] == facts.model_dump(mode="json")
    assert event["execution_status"] == "NO TRIGGER OR FILL INFERENCE"
    assert event["scoring"] == "NOT SCORED"
    assert event["event_id"] + ".json" in str(tmp_path / "forward-facts" / (event["event_id"] + ".json"))
    assert shadow_facts.audit_forward_fact_event(
        tmp_path, item, packages, facts, fact_packages,
    ) == event


def test_audit_rejects_tampering_and_duplicate_fields(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, available = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(shadow_facts, "_now", lambda: available + timedelta(minutes=1))
    path = shadow_facts.append_forward_fact_event(tmp_path, item, packages, facts, fact_packages)
    original = path.read_text()
    path.write_text('{"scoring":"SCORED",' + original[1:])
    with pytest.raises(ValueError, match="duplicate fact-event field"):
        shadow_facts.audit_forward_fact_event(tmp_path, item, packages, facts, fact_packages)


def test_rejects_facts_before_bar_availability(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, available = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(shadow_facts, "_now", lambda: available - timedelta(seconds=1))
    with pytest.raises(ValueError, match="not yet observable"):
        shadow_facts.append_forward_fact_event(tmp_path, item, packages, facts, fact_packages)


def test_rejects_candidate_identity_substitution(tmp_path, monkeypatch):
    item, packages, facts, fact_packages, available = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(shadow_facts, "_now", lambda: available + timedelta(minutes=1))
    changed = facts.model_copy(update={
        "identity": facts.identity.model_copy(update={"ticker": "TWTR"}),
        "bars": (facts.bars[0].model_copy(update={"ticker": "TWTR"}),),
    })
    with pytest.raises(ValueError, match="does not bind"):
        shadow_facts.append_forward_fact_event(tmp_path, item, packages, changed, fact_packages)


def test_rejects_authenticated_package_with_insufficient_semantic_scope(tmp_path, monkeypatch):
    item, packages, facts, _, available = prepared(tmp_path, monkeypatch)
    reference = ShadowEvidenceReference(
        evidence_id="d" * 64, source_authority="official fixture authority",
        source_locator="fixture://insufficient", artifact_sha256="d" * 64,
        available_at=available,
    )
    insufficient = package("d", reference)
    changed = facts.model_copy(update={
        "session": facts.session.model_copy(update={"evidence_package_id": insufficient.identity}),
        "identity": facts.identity.model_copy(update={"evidence_package_id": insufficient.identity}),
        "action_coverage": facts.action_coverage.model_copy(
            update={"evidence_package_id": insufficient.identity}
        ),
        "bars": (facts.bars[0].model_copy(update={"evidence_package_id": insufficient.identity}),),
    })
    monkeypatch.setattr(shadow_facts, "_now", lambda: available + timedelta(minutes=1))
    with pytest.raises(ValueError, match="required fact fields"):
        shadow_facts.append_forward_fact_event(
            tmp_path, item, packages, changed, (insufficient,),
        )


@pytest.mark.parametrize("change,match", [
    ({"sequence": 0}, "greater than or equal"),
    ({"interval_start": None}, "datetime.timezone.utc"),
    ({"price_basis": "ADJUSTED"}, "RAW_UNADJUSTED"),
    ({"high": Decimal("98")}, "incoherent raw OHLC"),
])
def test_raw_bars_fail_closed(change, match):
    item, _ = authenticated_watchlist()
    start = item.session.opens_at
    values = dict(
        instrument_id=item.candidates[0].instrument_id, ticker="IBM",
        market_date=item.session.market_date, sequence=1, interval_start=start,
        interval_end=start + timedelta(minutes=5), available_at=start + timedelta(minutes=6),
        is_final=True, price_basis="RAW_UNADJUSTED", open=Decimal("100"),
        high=Decimal("102"), low=Decimal("99"), close=Decimal("101"), volume=1,
        source_row="row", evidence_package_id="c" * 64,
    )
    if "interval_start" in change and change["interval_start"] is None:
        change = {"interval_start": start.replace(tzinfo=None)}
    with pytest.raises(ValidationError, match=match):
        ForwardRawBar(**(values | change))


def test_bundle_rejects_non_opening_origin_and_incomplete_actions(tmp_path, monkeypatch):
    _, _, facts, _, _ = prepared(tmp_path, monkeypatch)
    with pytest.raises(ValidationError, match="originate"):
        ForwardFactBundle(**(facts.model_dump(mode="python") | {
            "bars": (facts.bars[0].model_copy(update={
                "interval_start": facts.bars[0].interval_start + timedelta(minutes=1),
            }),),
        }))
    with pytest.raises(ValidationError, match="COMPLETE"):
        ForwardActionCoverage(**(facts.action_coverage.model_dump(mode="python") | {
            "status": "UNKNOWN",
        }))
