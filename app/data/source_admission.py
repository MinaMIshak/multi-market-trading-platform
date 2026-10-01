"""Vendor-neutral daily market-data source admission registry.

A declaration records what the repository knows about a daily OHLCV source:
market, access class, entitlement review, delay semantics and evidence. It is
not a transport and never fetches. Validated canonical bars from a source are
usable as authentic observations only when that source is ADMITTED here;
anything undeclared, unreviewed, paid, or for another market is
EVIDENCE_BLOCKED. An anonymous HTTP 200 never establishes usage rights.

No EGX daily source is admitted in this revision: add an admission only with a
reviewed entitlement evidence reference, never from availability alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


ADMITTED = "ADMITTED"
EVIDENCE_BLOCKED = "EVIDENCE_BLOCKED"


class SourceAccess(StrEnum):
    ANONYMOUS_PUBLIC = "ANONYMOUS_PUBLIC"
    AUTHENTICATED = "AUTHENTICATED"
    PAID_SUBSCRIPTION = "PAID_SUBSCRIPTION"
    UNOFFICIAL_CLIENT = "UNOFFICIAL_CLIENT"


class EntitlementStatus(StrEnum):
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    REVIEWED_PAPER_SHADOW = "REVIEWED_PAPER_SHADOW"
    # Explicit, recorded operator decision (docs/OPERATOR_DECISIONS.md) to use
    # a free source for internal Paper/Shadow research WITHOUT any contractual
    # licence. Admits the source; the "no licence" label travels with it.
    OPERATOR_ACCEPTED_UNLICENSED = "OPERATOR_ACCEPTED_UNLICENSED"
    DENIED = "DENIED"


class DataDelay(StrEnum):
    END_OF_DAY = "END_OF_DAY"
    DELAYED = "DELAYED"
    UNKNOWN = "UNKNOWN"


# Only free access classes may carry an admitted paper/shadow entitlement.
_ADMISSIBLE_ACCESS = frozenset({
    SourceAccess.ANONYMOUS_PUBLIC,
    SourceAccess.AUTHENTICATED,
})


@dataclass(frozen=True)
class DailySourceDeclaration:
    provider: str
    market: str
    access: SourceAccess
    entitlement: EntitlementStatus
    delay: DataDelay
    source_timezone: str
    evidence: str

    def __post_init__(self) -> None:
        for name in ("provider", "market", "source_timezone", "evidence"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError(f"source declaration {name} required")
        if self.provider != self.provider.lower() or self.provider == "canonical":
            raise ValueError("canonical lowercase provider identity required")
        if self.market not in ("EGX", "US"):
            raise ValueError("unknown source declaration market")
        for name, kind in (("access", SourceAccess),
                           ("entitlement", EntitlementStatus),
                           ("delay", DataDelay)):
            if type(getattr(self, name)) is not kind:
                raise ValueError(f"source declaration {name} must be {kind.__name__}")
        if (self.entitlement == EntitlementStatus.REVIEWED_PAPER_SHADOW
                and self.access not in _ADMISSIBLE_ACCESS):
            raise ValueError("reviewed entitlement requires a free access class")
        if (self.entitlement == EntitlementStatus.REVIEWED_PAPER_SHADOW
                and self.delay == DataDelay.UNKNOWN):
            raise ValueError("reviewed entitlement requires declared delay semantics")
        if self.entitlement == EntitlementStatus.OPERATOR_ACCEPTED_UNLICENSED:
            if self.access not in (SourceAccess.UNOFFICIAL_CLIENT, SourceAccess.ANONYMOUS_PUBLIC):
                raise ValueError("operator acceptance applies only to free unlicensed access")
            if self.delay == DataDelay.UNKNOWN:
                raise ValueError("operator acceptance requires declared delay semantics")
            if "operator decision" not in self.evidence.lower():
                raise ValueError("operator acceptance must cite the recorded operator decision")


_QUALIFICATION = "docs/ER1B_FREE_SOURCE_QUALIFICATION.md"
_MARKET_WATCH_QUALIFICATION = "docs/EGX_OFFICIAL_MARKET_WATCH_QUALIFICATION.md"
_TWELVE_DATA_RUNBOOK = "docs/TWELVE_DATA_PROVIDER.md"

# Repository-recorded findings only; none establishes a reviewed entitlement.
DAILY_SOURCE_DECLARATIONS: tuple[DailySourceDeclaration, ...] = (
    DailySourceDeclaration(
        provider="egid",
        market="EGX",
        access=SourceAccess.AUTHENTICATED,
        entitlement=EntitlementStatus.NOT_ESTABLISHED,
        delay=DataDelay.DELAYED,
        source_timezone="Africa/Cairo",
        evidence=("getSymbolHistory returned HTTP 401 without credentials; "
                  "subscription terms not accepted (" + _QUALIFICATION + ")"),
    ),
    DailySourceDeclaration(
        provider="eodhd",
        market="EGX",
        access=SourceAccess.PAID_SUBSCRIPTION,
        entitlement=EntitlementStatus.NOT_ESTABLISHED,
        delay=DataDelay.END_OF_DAY,
        source_timezone="Africa/Cairo",
        evidence=("paid token-gated API; final operation must not require paid "
                  "subscriptions (PROJECT_AUDIT.md)"),
    ),
    DailySourceDeclaration(
        provider="twelve_data",
        market="EGX",
        access=SourceAccess.AUTHENTICATED,
        entitlement=EntitlementStatus.NOT_ESTABLISHED,
        delay=DataDelay.END_OF_DAY,
        source_timezone="Africa/Cairo",
        evidence=("operator-selected primary EGX provider (2026-10-01); XCAI reference "
                  "lists 266 equities, all mapped by ISIN; no API key configured and the "
                  "plan/terms covering XCAI daily data for internal paper/shadow use are "
                  "not yet reviewed (" + _TWELVE_DATA_RUNBOOK + ")"),
    ),
    DailySourceDeclaration(
        provider="egx_official_market_watch",
        market="EGX",
        access=SourceAccess.ANONYMOUS_PUBLIC,
        entitlement=EntitlementStatus.NOT_ESTABLISHED,
        delay=DataDelay.END_OF_DAY,
        source_timezone="Africa/Cairo",
        evidence=("official beta.egx.com.eg market-watch endpoint is technically "
                  "reachable anonymously (216 securities, OHLCV with lastTradeDate, "
                  "2026-09-30); the site links no terms of use and no reviewed paper/"
                  "shadow usage right exists (" + _MARKET_WATCH_QUALIFICATION + ")"),
    ),
    DailySourceDeclaration(
        provider="tradingview_tvdatafeed",
        market="EGX",
        access=SourceAccess.UNOFFICIAL_CLIENT,
        entitlement=EntitlementStatus.NOT_ESTABLISHED,
        delay=DataDelay.UNKNOWN,
        source_timezone="Africa/Cairo",
        evidence="unofficial client; TradingView rights unreviewed (PROJECT_AUDIT.md)",
    ),
    DailySourceDeclaration(
        provider="tradingview_tvdatafeed_egx",
        market="EGX",
        access=SourceAccess.UNOFFICIAL_CLIENT,
        entitlement=EntitlementStatus.OPERATOR_ACCEPTED_UNLICENSED,
        delay=DataDelay.END_OF_DAY,
        source_timezone="Africa/Cairo",
        evidence=("operator decision 2026-10-01 (zero-cost data strategy, "
                  "docs/OPERATOR_DECISIONS.md): primary EGX daily source for internal "
                  "Paper/Shadow research only; anonymous unofficial client of TradingView "
                  "(data vendor ICE), split-adjusted series, completed sessions only, official "
                  "EGX market-watch cross-check; no contractual licence or entitlement claimed"),
    ),
)


@dataclass(frozen=True)
class DailySourceAdmission:
    provider: str
    market: str
    status: str
    reason: str
    declaration: DailySourceDeclaration | None


class DailySourceRegistry:
    def __init__(
        self,
        declarations: tuple[DailySourceDeclaration, ...] = DAILY_SOURCE_DECLARATIONS,
    ) -> None:
        index: dict[tuple[str, str], DailySourceDeclaration] = {}
        for item in declarations:
            if type(item) is not DailySourceDeclaration:
                raise ValueError("daily source declaration required")
            key = (item.provider, item.market)
            if key in index:
                raise ValueError("duplicate daily source declaration")
            index[key] = item
        self._index = index

    def admission(self, provider: object, market: object) -> DailySourceAdmission:
        """Resolve exact identities only; never normalize or guess a source."""
        provider_text = provider if type(provider) is str else ""
        market_text = market if type(market) is str else ""
        declaration = self._index.get((provider_text, market_text))

        def blocked(reason: str) -> DailySourceAdmission:
            return DailySourceAdmission(provider_text, market_text,
                                        EVIDENCE_BLOCKED, reason, declaration)

        if declaration is None:
            return blocked("undeclared daily source for market")
        if declaration.entitlement == EntitlementStatus.DENIED:
            return blocked("source entitlement denied")
        if declaration.access == SourceAccess.PAID_SUBSCRIPTION:
            return blocked("paid subscription source not admissible")
        if declaration.entitlement == EntitlementStatus.OPERATOR_ACCEPTED_UNLICENSED:
            return DailySourceAdmission(
                provider_text, market_text, ADMITTED,
                "operator-accepted for internal Paper/Shadow research; no contractual licence",
                declaration)
        if declaration.entitlement != EntitlementStatus.REVIEWED_PAPER_SHADOW:
            return blocked("source entitlement not established")
        return DailySourceAdmission(provider_text, market_text, ADMITTED,
                                    "reviewed paper/shadow entitlement", declaration)


def licensing_label(declaration) -> str:
    """Truthful rights label for operator views; never implies a licence that does not exist."""
    if declaration is None:
        return "UNDECLARED"
    return {
        EntitlementStatus.REVIEWED_PAPER_SHADOW: "REVIEWED_PAPER_SHADOW_ENTITLEMENT",
        EntitlementStatus.OPERATOR_ACCEPTED_UNLICENSED: "NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED",
        EntitlementStatus.DENIED: "DENIED",
    }.get(declaration.entitlement, "NOT_ESTABLISHED")


DEFAULT_DAILY_SOURCE_REGISTRY = DailySourceRegistry()


def daily_source_admission(provider: object, market: object) -> DailySourceAdmission:
    return DEFAULT_DAILY_SOURCE_REGISTRY.admission(provider, market)


def daily_source_summary(
    registry: DailySourceRegistry = DEFAULT_DAILY_SOURCE_REGISTRY,
) -> list[dict]:
    """Operator view of every declaration and its resolved admission."""
    rows = []
    for (provider, market), item in sorted(registry._index.items()):
        admission = registry.admission(provider, market)
        rows.append({
            "provider": provider, "market": market,
            "access": item.access.value, "entitlement": item.entitlement.value,
            "delay": item.delay.value, "source_timezone": item.source_timezone,
            "evidence": item.evidence,
            "status": admission.status, "reason": admission.reason,
            "licensing": licensing_label(item),
        })
    return rows
