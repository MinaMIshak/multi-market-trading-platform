"""Artificial security-type bindings; no ETF comparison or broker claim."""

from datetime import timedelta
import json

import pytest

from app import main
from app.paper import (
    shadow_collection,
    shadow_freeze,
    shadow_ledger,
    shadow_security_type,
)
from app.paper.shadow_security_type import (
    audit_security_type_binding,
    record_security_type_binding,
)
from app.us.contracts import (
    USListingIdentity,
    USSecurityType,
)
from app.us.historical_identity import (
    HistoricalUSListingFact,
    US_LISTING_EVIDENCE_FIELDS,
)
from tests.test_shadow_collection import (
    authenticated_watchlist,
)
from tests.test_us1_historical_identity import (
    _package,
)


def snapshot(directory):
    return {
        path: path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def prepared_binding(
    tmp_path,
    monkeypatch,
    *,
    security_type=USSecurityType.ETF,
):
    item, packages = authenticated_watchlist()

    assert item.session.market == "US"
    assert item.session.calendar_mic in ("XNYS", "XNAS")

    candidate = item.candidates[0]
    assert candidate.identity_status == "KNOWN"
    assert candidate.instrument_id is not None

    window = (
        item.session.decision_cutoff
        - item.generated_at
    )
    quarter = window / 4

    completed_at = item.generated_at + quarter
    ledger_at = item.generated_at + quarter * 2
    classification_at = (
        item.generated_at + quarter * 3
    )

    monkeypatch.setattr(
        shadow_collection,
        "_now",
        lambda: completed_at,
    )
    monkeypatch.setattr(
        shadow_freeze,
        "_now",
        lambda: completed_at,
    )

    shadow_collection.complete_watchlist(
        tmp_path,
        item,
        packages,
    )

    monkeypatch.setattr(
        shadow_ledger,
        "_now",
        lambda: ledger_at,
    )
    shadow_ledger.append_candidate_event(
        tmp_path,
        item,
        packages,
    )

    evidence = _package(
        provider="fixture-provider",
        available_at=item.information_cutoff,
        received_at=item.generated_at
        - timedelta(seconds=2),
        reviewed_at=item.generated_at
        - timedelta(seconds=1),
        covered_fields=sorted(
            US_LISTING_EVIDENCE_FIELDS
        ),
        sha_char="9",
    )

    listing = USListingIdentity(
        effective_date=item.session.market_date,
        instrument_id=candidate.instrument_id,
        canonical_symbol=candidate.ticker,
        listing_mic=item.session.calendar_mic,
        security_type=security_type,
        provider_symbol=candidate.ticker,
        source_provider="fixture-provider",
        source_instrument_key=(
            "fixture-security-type-key"
        ),
        is_primary_listing=True,
    )

    fact = HistoricalUSListingFact(
        listing=listing,
        evidence_package=evidence,
    )

    monkeypatch.setattr(
        shadow_security_type,
        "_now",
        lambda: classification_at,
    )

    return (
        item,
        packages,
        candidate,
        fact,
        classification_at,
    )


def publish_ui(
    directory,
    monkeypatch,
    item,
    packages,
    candidate,
    fact,
):
    (directory / "input.json").write_text(
        json.dumps(
            {
                "schema_version":
                    "shadow-ui-input-v1",
                "watchlist":
                    item.model_dump(mode="json"),
                "evidence_packages": [
                    package.model_dump(mode="json")
                    for package in packages
                ],
            }
        )
    )

    (directory / "security-type.json").write_text(
        json.dumps(
            {
                "schema_version":
                    "shadow-ui-security-types-v1",
                "bindings": [
                    {
                        "candidate_id":
                            candidate.candidate_id,
                        "fact":
                            fact.model_dump(mode="json"),
                    }
                ],
            }
        )
    )

    monkeypatch.setenv(
        "EGX_SHADOW_DIRECTORY",
        str(directory),
    )


@pytest.mark.parametrize(
    "security_type",
    [
        USSecurityType.COMMON_STOCK,
        USSecurityType.ETF,
    ],
)
def test_exact_dated_security_type_receipt_is_auditable(
    tmp_path,
    monkeypatch,
    security_type,
):
    (
        item,
        packages,
        candidate,
        fact,
        _,
    ) = prepared_binding(
        tmp_path,
        monkeypatch,
        security_type=security_type,
    )

    path = record_security_type_binding(
        tmp_path,
        item,
        packages,
        candidate.candidate_id,
        fact,
    )

    receipt = json.loads(path.read_bytes())

    assert receipt["security_type"] == security_type.value
    assert receipt["status"] == (
        "AUDITED EXACT-DATED SECURITY TYPE "
        "/ NOT AN ETF COMPARISON"
    )
    assert receipt["comparison_status"] == (
        "UNAVAILABLE / NO COMPARABLE ETF EVIDENCE"
    )
    assert receipt["instrument_id"] == str(
        candidate.instrument_id
    )
    assert receipt["ticker"] == candidate.ticker

    audited = audit_security_type_binding(
        tmp_path,
        item,
        packages,
        candidate.candidate_id,
        fact,
    )

    assert audited == receipt


@pytest.mark.parametrize(
    "damage",
    [
        "date",
        "instrument",
        "symbol",
        "mic",
        "late-review",
    ],
)
def test_binding_rejects_nonexact_or_late_identity_evidence(
    tmp_path,
    monkeypatch,
    damage,
):
    (
        item,
        packages,
        candidate,
        fact,
        _,
    ) = prepared_binding(
        tmp_path,
        monkeypatch,
    )

    listing = fact.listing
    evidence = fact.evidence_package

    if damage == "date":
        listing = listing.model_copy(
            update={
                "effective_date":
                    item.session.market_date
                    - timedelta(days=1)
            }
        )
    elif damage == "instrument":
        other = authenticated_watchlist()[0]
        # UUID remains exact but must not bind this candidate.
        listing = listing.model_copy(
            update={
                "instrument_id":
                    other.candidates[0]
                    .instrument_id
            }
        )
        # If the fixture uses the same stable ID, force a distinct
        # valid UUID through the listing type itself.
        if (
            listing.instrument_id
            == candidate.instrument_id
        ):
            from uuid import uuid4
            listing = listing.model_copy(
                update={"instrument_id": uuid4()}
            )
    elif damage == "symbol":
        listing = listing.model_copy(
            update={"canonical_symbol": "OTHER"}
        )
    elif damage == "mic":
        alternate = (
            "XNYS"
            if item.session.calendar_mic == "XNAS"
            else "XNAS"
        )
        listing = listing.model_copy(
            update={"listing_mic": alternate}
        )
    else:
        evidence = _package(
            provider="fixture-provider",
            available_at=item.information_cutoff,
            received_at=item.generated_at
            - timedelta(seconds=1),
            reviewed_at=item.generated_at
            + timedelta(seconds=1),
            covered_fields=sorted(
                US_LISTING_EVIDENCE_FIELDS
            ),
            sha_char="8",
        )

    changed = HistoricalUSListingFact(
        listing=listing,
        evidence_package=evidence,
    )

    with pytest.raises(ValueError):
        record_security_type_binding(
            tmp_path,
            item,
            packages,
            candidate.candidate_id,
            changed,
        )


def test_shadow_api_and_page_surface_only_audited_classification(
    tmp_path,
    monkeypatch,
):
    (
        item,
        packages,
        candidate,
        fact,
        _,
    ) = prepared_binding(
        tmp_path,
        monkeypatch,
        security_type=USSecurityType.ETF,
    )

    receipt = record_security_type_binding(
        tmp_path,
        item,
        packages,
        candidate.candidate_id,
        fact,
    )

    publish_ui(
        tmp_path,
        monkeypatch,
        item,
        packages,
        candidate,
        fact,
    )

    before = snapshot(tmp_path)

    state = main.shadow()

    assert state["available"] is True
    assert state["security_types"] is not None

    binding = state[
        "security_types"
    ]["bindings"][candidate.candidate_id]

    assert binding["security_type"] == "ETF"
    assert binding["status"] == (
        "AUDITED EXACT-DATED SECURITY TYPE "
        "/ NOT AN ETF COMPARISON"
    )
    assert binding["comparison_status"] == (
        "UNAVAILABLE / NO COMPARABLE ETF EVIDENCE"
    )

    body = main.shadow_page().body.decode()

    assert "<dd>ETF</dd>" in body
    assert (
        "AUDITED EXACT-DATED SECURITY TYPE "
        "/ NOT AN ETF COMPARISON"
    ) in body
    assert "UNAVAILABLE / NO AUDITED PORTFOLIO SNAPSHOT" in body
    assert "Stock / ETF allocation comparison" not in body

    assert snapshot(tmp_path) == before

    # Damaging only classification truth must fail that surface
    # closed while preserving the independently audited collection.
    receipt.write_text("{}")

    damaged = snapshot(tmp_path)
    state = main.shadow()

    assert state["available"] is True
    assert state["collection"] is not None
    assert state["security_types"] is None

    body = main.shadow_page().body.decode()

    assert (
        "UNAVAILABLE / NOT AUTHENTICATED"
    ) in body
    assert (
        "AUDITED EXACT-DATED SECURITY TYPE "
        "/ NOT AN ETF COMPARISON"
    ) not in body

    assert snapshot(tmp_path) == damaged


def test_ui_transport_cannot_change_authenticated_security_type(
    tmp_path,
    monkeypatch,
):
    (
        item,
        packages,
        candidate,
        fact,
        _,
    ) = prepared_binding(
        tmp_path,
        monkeypatch,
        security_type=USSecurityType.COMMON_STOCK,
    )

    record_security_type_binding(
        tmp_path,
        item,
        packages,
        candidate.candidate_id,
        fact,
    )

    publish_ui(
        tmp_path,
        monkeypatch,
        item,
        packages,
        candidate,
        fact,
    )

    assert (
        main.shadow()["security_types"]
        ["bindings"][candidate.candidate_id]
        ["security_type"]
        == "COMMON_STOCK"
    )

    path = tmp_path / "security-type.json"
    document = json.loads(path.read_bytes())
    document["bindings"][0]["fact"][
        "listing"
    ]["security_type"] = "ETF"
    path.write_text(json.dumps(document))

    state = main.shadow()

    assert state["available"] is True
    assert state["security_types"] is None
