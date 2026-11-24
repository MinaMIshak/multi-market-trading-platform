"""US new-listing admission (US-NEW-LISTING-v1): a separate, explicit population.

The normal research universe (``app.us.universe.build_universe``) ranks by
30-day average dollar volume. Recently listed common stocks may sit outside it
only because their liquidity history is short. This module admits them into a
*distinct* ``NEW_LISTING`` population under an explicit, versioned policy.

Admission authority is the Nasdaq Trader symbol directory only: a symbol is
"new to the directory" when it is present (non-test) in the current master and
absent from a baseline master observed a policy-bounded window earlier. The
TradingView scanner is never an admission source; it only *refines* (primary
common stock on NASDAQ/NYSE/AMEX) and supplies a provisional liquidity figure.
A ticker recognized by the scanner but missing from the current master is never
considered.

Exclusions follow the declared policy: test issues are already dropped by
``parse_master``; ETFs in the master, symbols without a primary-common scanner
row, symbols below the declared provisional liquidity floor and symbols beyond
the declared member cap are excluded and counted.

Transition to the normal universe is deterministic:

* a symbol that is a member of the supplied normal universe is reported in
  ``in_normal_universe`` and never in the new-listing population;
* the new-listing status ends once the baseline master (observed a full policy
  window earlier) contains the symbol.

Interpretation is deliberately limited: absence from the baseline does not
prove an IPO (a rename, transfer or relisting looks identical), so
``ipo_interpretation`` is ``NOT_ESTABLISHED``. Admission never implies trading
eligibility (``trading_eligibility`` is ``NOT_ESTABLISHED``). Pure: no IO,
network, clock, store, job or caller. PAPER/SHADOW only; LIVE_MONEY=false.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.us.universe import VERSION as NORMAL_UNIVERSE_VERSION
from app.us.universe import UniverseError, normalize

VERSION = "US-NEW-LISTING-v1"
POLICY_VERSION = "us-new-listing-policy-v1"
POPULATION = "NEW_LISTING"
MASTER_FIELDS = frozenset({"name", "exchange", "etf", "financial_status"})
EXCLUSION_REASONS = ("ETF_IN_MASTER", "NOT_COMMON_PRIMARY_IN_SCANNER", "BELOW_NEW_LISTING_LIQUIDITY_FLOOR",
                     "BEYOND_MAX_MEMBERS")
SCANNER_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX"})


@dataclass(frozen=True)
class NewListingPolicy:
    """Declared, unvalidated new-listing policy. Every threshold is explicit."""

    version: str = POLICY_VERSION
    min_window_days: int = 28
    max_window_days: int = 60
    min_avg_dollar_volume: Decimal = Decimal("1000000")
    max_members: int = 100
    max_new_to_directory: int = 1000
    basis: str = "DECLARED_UNVALIDATED"

    def check(self) -> None:
        if self.version != POLICY_VERSION or self.basis != "DECLARED_UNVALIDATED":
            raise UniverseError("unsupported new-listing policy version or basis")
        for name in ("min_window_days", "max_window_days", "max_members", "max_new_to_directory"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise UniverseError(f"new-listing policy {name} must be a positive int")
        if self.min_window_days > self.max_window_days or self.max_window_days > 366:
            raise UniverseError("new-listing policy window is inconsistent")
        floor = self.min_avg_dollar_volume
        if type(floor) is not Decimal or not floor.is_finite() or floor < 0:
            raise UniverseError("new-listing policy min_avg_dollar_volume must be a finite Decimal >= 0")

    def as_dict(self) -> dict:
        return {"version": self.version, "min_window_days": self.min_window_days,
                "max_window_days": self.max_window_days, "min_avg_dollar_volume": str(self.min_avg_dollar_volume),
                "max_members": self.max_members, "max_new_to_directory": self.max_new_to_directory,
                "basis": self.basis}


def _check_master(master, label: str) -> None:
    if type(master) is not dict or not master:
        raise UniverseError(f"{label} master must be a non-empty dict")
    for symbol, listing in master.items():
        if type(symbol) is not str or normalize(symbol) != symbol or not symbol:
            raise UniverseError(f"{label} master symbol {symbol!r} is not normalized")
        if type(listing) is not dict or set(listing) != MASTER_FIELDS or type(listing["etf"]) is not bool:
            raise UniverseError(f"{label} master listing for {symbol} is not a parse_master listing")


def _aware(value, label: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise UniverseError(f"{label} must be an aware datetime")
    return value


def _scanner_index(scanner_rows) -> dict:
    if type(scanner_rows) is not list:
        raise UniverseError("scanner rows must be a list from parse_scanner")
    index = {}
    for row in scanner_rows:
        if type(row) is not dict:
            raise UniverseError("scanner row must be a dict")
        ticker, volume, close = row.get("ticker"), row.get("avg_dollar_volume_30d"), row.get("close")
        if type(ticker) is not str or normalize(ticker) != ticker or not ticker:
            raise UniverseError(f"scanner ticker {ticker!r} is not normalized")
        if type(volume) is not Decimal or type(close) is not Decimal or not volume.is_finite() or volume <= 0 \
                or not close.is_finite() or close <= 0:
            raise UniverseError(f"scanner row {ticker} lacks positive Decimal liquidity")
        if ticker in index:
            # Two scanner rows for one ticker are ambiguous; fail closed rather than pick one.
            raise UniverseError(f"duplicate scanner rows for {ticker}")
        index[ticker] = row
    return index


def _normal_members(normal_universe) -> frozenset:
    if type(normal_universe) is not dict or normal_universe.get("version") != NORMAL_UNIVERSE_VERSION \
            or type(normal_universe.get("members")) is not list:
        raise UniverseError(f"normal universe must be a {NORMAL_UNIVERSE_VERSION} document")
    tickers = [member.get("ticker") if type(member) is dict else None for member in normal_universe["members"]]
    if any(type(t) is not str for t in tickers) or len(set(tickers)) != len(tickers):
        raise UniverseError("normal universe members must have unique tickers")
    return frozenset(tickers)


def build_new_listing_admissions(baseline_master: dict, current_master: dict, scanner_rows: list,
                                 normal_universe: dict, *, baseline_observed_at: datetime,
                                 current_observed_at: datetime, policy: NewListingPolicy | None = None) -> dict:
    """Return the US-NEW-LISTING-v1 population document; fail closed on any doubt.

    ``baseline_master``/``current_master`` are ``parse_master`` outputs of two
    retained Nasdaq Trader directory observations; ``scanner_rows`` is the
    ``parse_scanner`` output observed with the current master; and
    ``normal_universe`` is the ``build_universe`` document built from them.
    Observation times are caller-supplied and recorded as such.
    """
    policy = NewListingPolicy() if policy is None else policy
    if type(policy) is not NewListingPolicy:
        raise UniverseError("policy must be a NewListingPolicy")
    policy.check()
    _check_master(baseline_master, "baseline")
    _check_master(current_master, "current")
    baseline_at = _aware(baseline_observed_at, "baseline_observed_at")
    current_at = _aware(current_observed_at, "current_observed_at")
    window_seconds = int((current_at - baseline_at).total_seconds())
    if not policy.min_window_days * 86400 <= window_seconds <= policy.max_window_days * 86400:
        raise UniverseError("baseline observation is outside the declared new-listing window")
    scanner = _scanner_index(scanner_rows)
    normal = _normal_members(normal_universe)

    new_to_directory = sorted(set(current_master) - set(baseline_master))
    if len(new_to_directory) > policy.max_new_to_directory:
        # An implausibly large difference suggests a partial/mismatched baseline, not a listing wave.
        raise UniverseError(f"implausible new-to-directory count ({len(new_to_directory)})")

    excluded = {reason: 0 for reason in EXCLUSION_REASONS}
    in_normal, qualified = [], []
    for symbol in new_to_directory:
        listing = current_master[symbol]
        if symbol in normal:
            in_normal.append(symbol)
            continue
        if listing["etf"]:
            excluded["ETF_IN_MASTER"] += 1
            continue
        row = scanner.get(symbol)
        if row is None or row.get("exchange") not in SCANNER_EXCHANGES:
            excluded["NOT_COMMON_PRIMARY_IN_SCANNER"] += 1
            continue
        if row["avg_dollar_volume_30d"] < policy.min_avg_dollar_volume:
            excluded["BELOW_NEW_LISTING_LIQUIDITY_FLOOR"] += 1
            continue
        qualified.append((row, listing))

    qualified.sort(key=lambda pair: (-pair[0]["avg_dollar_volume_30d"], pair[0]["ticker"]))
    excluded["BEYOND_MAX_MEMBERS"] = max(0, len(qualified) - policy.max_members)
    members = [{"ticker": row["ticker"], "tv_symbol": row.get("tv_symbol"), "exchange": row["exchange"],
                "name": row.get("name"), "isin": row.get("isin"), "sector": row.get("sector"),
                "avg_dollar_volume_30d": str(row["avg_dollar_volume_30d"].quantize(Decimal(1))),
                "close": str(row["close"]), "listing_exchange": listing["exchange"],
                "listing_name": listing["name"], "financial_status": listing["financial_status"],
                "population": POPULATION}
               for row, listing in qualified[:policy.max_members]]
    return {
        "version": VERSION,
        "population": POPULATION,
        "policy": policy.as_dict(),
        "baseline_observed_at": baseline_at.isoformat(),
        "current_observed_at": current_at.isoformat(),
        "observation_clock": "CALLER_SUPPLIED_UNVERIFIED",
        "window_seconds": window_seconds,
        "baseline_master_symbols": len(baseline_master),
        "current_master_symbols": len(current_master),
        "new_to_directory": len(new_to_directory),
        "members": members,
        "excluded": excluded,
        "in_normal_universe": in_normal,
        "normal_universe_version": NORMAL_UNIVERSE_VERSION,
        "admission_source": "NASDAQ_TRADER_DIRECTORY_ONLY",
        "scanner_role": "REFINEMENT_AND_PROVISIONAL_LIQUIDITY_ONLY",
        "liquidity_basis": "SCANNER_AVERAGE_VOLUME_30D_OVER_AVAILABLE_HISTORY_UNVERIFIED",
        "new_to_directory_basis": "ABSENT_FROM_BASELINE_MASTER",
        "ipo_interpretation": "NOT_ESTABLISHED",
        "transition_rule": "NORMAL_UNIVERSE_MEMBERSHIP_WINS_THEN_ENDS_WHEN_BASELINE_CONTAINS_SYMBOL",
        "trading_eligibility": "NOT_ESTABLISHED",
        "trading_effect": "NONE",
        "mode": "SHADOW",
        "live_money": False,
        "rule": (f"symbols present, non-test and non-ETF in the current Nasdaq Trader directories but absent from "
                 f"a baseline observed {policy.min_window_days}-{policy.max_window_days} days earlier, refined to "
                 f"primary common stocks in the TradingView scanner with provisional average dollar volume >= "
                 f"{policy.min_avg_dollar_volume}, excluding normal-universe members; top {policy.max_members}; "
                 "separate NEW_LISTING population, no trading eligibility"),
    }
