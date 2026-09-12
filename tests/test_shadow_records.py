"""Artificial forward-record fixtures only; no market evidence or recommendations."""
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.paper import shadow_freeze
from app.paper.shadow_records import (
    ShadowCandidate, ShadowEvidenceReference, ShadowSession, ShadowWatchlist,
    freeze_watchlist,
)


AT = datetime(2026, 9, 12, 20, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64


def evidence(identity=HASH_A, available_at=AT - timedelta(hours=2)):
    return ShadowEvidenceReference(
        evidence_id=identity, source_authority="official fixture authority",
        source_locator="fixture://artifact", artifact_sha256=identity,
        available_at=available_at,
    )


def candidate(**changes):
    values = dict(
        candidate_id="fixture-ibm", decision_status="BUY_CANDIDATE",
        identity_status="KNOWN", instrument_id=UUID(int=1), ticker="IBM",
        thesis="artificial fixture thesis", context=("fixture context",),
        technical_setup="fixture setup", entry_condition="fixture condition",
        entry_rule="LONG_ENTRY_ZONE_TOUCH_V1",
        entry_low=Decimal("100"), entry_high=Decimal("101"), stop=Decimal("95"),
        targets=(Decimal("110"), Decimal("115")),
        expected_holding_window="NOT_YET_VALIDATED", confidence="EXPERIMENTAL",
        probability="NOT_YET_VALIDATED", liquidity_status="PASS",
        liquidity_reason="artificial capacity fixture", paper_quantity=1,
        paper_risk_status="APPROVED", evidence_ids=(HASH_B,),
    )
    return ShadowCandidate(**(values | changes))


def watchlist(**changes):
    values = dict(
        record_id="US-20260914-fixture", generated_at=AT - timedelta(minutes=30),
        information_cutoff=AT - timedelta(hours=1),
        session=ShadowSession(
            market="US", market_date=date(2026, 9, 14), calendar_mic="XNYS",
            state="OPEN", opens_at=AT + timedelta(hours=42),
            decision_cutoff=AT + timedelta(hours=41), evidence_ids=(HASH_A,),
        ), evidence=(evidence(), evidence(HASH_B)), candidates=(candidate(),),
    )
    return ShadowWatchlist(**(values | changes))


def test_structured_watchlist_freezes_exact_canonical_document(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow_freeze, "_now", lambda: AT)
    item = watchlist()
    path = freeze_watchlist(tmp_path, item)
    envelope = shadow_freeze.audit_document(path)
    stored = json.loads(bytes.fromhex(envelope["document_hex"]))
    assert stored == item.model_dump(mode="json")
    assert envelope["admission"] == "UNADMITTED"
    assert envelope["scoring"] == "NOT SCORED"
    assert stored["label"] == "EXPERIMENTAL / PAPER ONLY"


@pytest.mark.parametrize("changes,match", [
    ({"instrument_id": None}, "stable identity"),
    ({"entry_low": Decimal("94")}, "geometry"),
    ({"targets": (Decimal("110"), Decimal("109"))}, "strictly increase"),
    ({"liquidity_status": "UNKNOWN"}, "passed liquidity"),
    ({"paper_quantity": 0}, "paper-risk size"),
    ({"probability": Decimal("0.9")}, "NOT_YET_VALIDATED"),
])
def test_buy_candidate_fails_closed(changes, match):
    with pytest.raises(ValidationError, match=match):
        candidate(**changes)


def test_watch_is_preserved_without_implied_position():
    item = candidate(
        decision_status="WATCH", entry_low=None, entry_high=None, stop=None,
        targets=(), liquidity_status="UNKNOWN", paper_quantity=0,
        paper_risk_status="NOT_EVALUATED",
    )
    assert item.paper_quantity == 0


@pytest.mark.parametrize("changes,match", [
    ({"generated_at": AT - timedelta(hours=2)}, "cutoff window"),
    ({"evidence": (evidence(),)}, "referenced exactly"),
    ({"evidence": (evidence(), evidence(HASH_B, AT),)}, "post-cutoff"),
    ({"candidates": (candidate(), candidate())}, "duplicate candidate"),
])
def test_watchlist_rejects_clock_and_evidence_failures(changes, match):
    with pytest.raises(ValidationError, match=match):
        watchlist(**changes)


def test_session_rejects_market_mic_mismatch_and_late_cutoff():
    base = watchlist().session.model_dump(mode="python")
    with pytest.raises(ValidationError, match="market/calendar"):
        ShadowSession(**(base | {"calendar_mic": "XCAI"}))
    with pytest.raises(ValidationError, match="cannot follow"):
        ShadowSession(**(base | {"decision_cutoff": AT + timedelta(hours=43)}))


def test_freeze_requires_exact_validated_record(tmp_path):
    with pytest.raises(ValueError, match="exact ShadowWatchlist"):
        freeze_watchlist(tmp_path, watchlist().model_dump())


def test_freeze_rejects_future_generation_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow_freeze, "_now", lambda: AT - timedelta(minutes=45))
    with pytest.raises(ValueError, match="future generated_at"):
        freeze_watchlist(tmp_path, watchlist())


def test_session_open_must_match_local_market_date():
    base = watchlist().session.model_dump(mode="python")
    with pytest.raises(ValidationError, match="local market date"):
        ShadowSession(**(base | {"market_date": date(2026, 9, 15)}))


def test_all_target_values_must_be_positive():
    with pytest.raises(ValidationError, match="positive finite"):
        candidate(decision_status="WATCH", targets=(Decimal("-1"),),
                  paper_quantity=0, paper_risk_status="NOT_EVALUATED")
