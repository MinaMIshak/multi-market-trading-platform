"""Offline registry contracts; no source is reviewed or fetched here."""
from dataclasses import replace

import pytest

from app.data.source_admission import (
    ADMITTED,
    DAILY_SOURCE_DECLARATIONS,
    EVIDENCE_BLOCKED,
    DailySourceDeclaration,
    DailySourceRegistry,
    DataDelay,
    EntitlementStatus,
    SourceAccess,
    daily_source_admission,
)


def declaration(**overrides):
    values = dict(provider="fixture_free", market="EGX",
                  access=SourceAccess.ANONYMOUS_PUBLIC,
                  entitlement=EntitlementStatus.REVIEWED_PAPER_SHADOW,
                  delay=DataDelay.END_OF_DAY, source_timezone="Africa/Cairo",
                  evidence="artificial fixture review reference")
    values.update(overrides)
    return DailySourceDeclaration(**values)


def test_no_egx_daily_source_is_admitted_in_repository_registry():
    for item in DAILY_SOURCE_DECLARATIONS:
        assert item.entitlement != EntitlementStatus.REVIEWED_PAPER_SHADOW
        assert daily_source_admission(item.provider, item.market).status == EVIDENCE_BLOCKED


@pytest.mark.parametrize("provider,reason", [
    ("egid", "source entitlement not established"),
    ("tradingview_tvdatafeed", "source entitlement not established"),
    ("tradingview_tvdatafeed_egx", "source entitlement not established"),
    ("egx_official_market_watch", "source entitlement not established"),
    ("twelve_data", "source entitlement not established"),
    ("eodhd", "paid subscription source not admissible"),
    ("unknown_feed", "undeclared daily source for market"),
])
def test_known_and_unknown_sources_are_evidence_blocked(provider, reason):
    result = daily_source_admission(provider, "EGX")
    assert (result.status, result.reason) == (EVIDENCE_BLOCKED, reason)


@pytest.mark.parametrize("provider,market", [
    ("EGID", "EGX"), (" egid", "EGX"), ("egid", "egx"), ("egid", "US"),
    (None, "EGX"), ("egid", None), (1, "EGX"),
])
def test_identities_are_exact_never_normalized(provider, market):
    result = daily_source_admission(provider, market)
    assert result.status == EVIDENCE_BLOCKED
    assert result.reason == "undeclared daily source for market"
    assert result.declaration is None


def test_reviewed_free_declaration_is_admitted_for_its_market_only():
    registry = DailySourceRegistry((declaration(),))
    assert registry.admission("fixture_free", "EGX").status == ADMITTED
    assert registry.admission("fixture_free", "US").status == EVIDENCE_BLOCKED


def test_denied_entitlement_is_blocked():
    registry = DailySourceRegistry((declaration(entitlement=EntitlementStatus.DENIED),))
    result = registry.admission("fixture_free", "EGX")
    assert (result.status, result.reason) == (EVIDENCE_BLOCKED, "source entitlement denied")


@pytest.mark.parametrize("access", [SourceAccess.PAID_SUBSCRIPTION,
                                    SourceAccess.UNOFFICIAL_CLIENT])
def test_reviewed_entitlement_requires_free_official_access(access):
    with pytest.raises(ValueError, match="free access class"):
        declaration(access=access)


def test_reviewed_entitlement_requires_declared_delay():
    with pytest.raises(ValueError, match="delay semantics"):
        declaration(delay=DataDelay.UNKNOWN)


@pytest.mark.parametrize("overrides", [
    {"provider": ""}, {"provider": "Fixture"}, {"provider": "canonical"},
    {"provider": " fixture"}, {"market": "EGYPT"}, {"evidence": ""},
    {"evidence": " padded "}, {"source_timezone": ""},
    {"access": "ANONYMOUS_PUBLIC"}, {"entitlement": "REVIEWED_PAPER_SHADOW"},
    {"delay": "END_OF_DAY"},
])
def test_declarations_reject_incomplete_or_untyped_fields(overrides):
    with pytest.raises(ValueError):
        declaration(**overrides)


def test_registry_rejects_duplicates_and_foreign_objects():
    with pytest.raises(ValueError, match="duplicate"):
        DailySourceRegistry((declaration(), declaration()))
    with pytest.raises(ValueError, match="declaration required"):
        DailySourceRegistry(({"provider": "fixture_free"},))
    # Same provider may be declared separately per market.
    registry = DailySourceRegistry((declaration(), replace(declaration(), market="US")))
    assert registry.admission("fixture_free", "US").status == ADMITTED
