"""Cross-check a provider's latest daily bar against official EGX market-watch evidence.

Verification only. The official market-watch source stays EVIDENCE_BLOCKED and
its values never become stored bars. A capture is usable for session D only
when its manifest says the official status was Closed, the session gate
verdict was COMPLETED_SESSION, and the gate's session date is D.

Policy EGX-XCHECK-v2, calibrated on the 2026-10-01 field study (TradingView
against the official post-close capture, n=419; docs/TRADINGVIEW_PROVIDER.md).
Open always agreed. TradingView's close tracks the official LAST TRADE
(``lastPrice``) better than the official weighted ``closePrice``. Its low is
systematically slightly lower, and volume definitions differ. So per symbol
(by ISIN) for session D:
- DISCREPANCY (material; the caller quarantines the symbol): open differs by
  more than 0.5 %, high by more than 2 %, low by more than 5 %, or close vs the
  official ``lastPrice`` (``closePrice`` if absent) by more than 2 %. These
  indicate a wrong symbol, wrong session or corrupted bar, not definitional
  variation;
- MINOR_DIFFERENCE: none material, but some field differs by more than
  ``tolerance`` (0.5 %); stored, and every difference is recorded;
- MATCH: every compared field within ``tolerance``;
- UNVERIFIED: no usable capture, the ISIN is absent, or the provider has no
  bar for D. This is not a failure and not a confirmation.
Volume and the official ``closePrice`` are recorded for reporting only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import glob
import json
from pathlib import Path

POLICY = "EGX-XCHECK-v2"
# (provider field, official field(s), material threshold)
MATERIAL = (("open", ("openPrice",), Decimal("0.005")),
            ("high", ("high",), Decimal("0.02")),
            ("low", ("low",), Decimal("0.05")),
            ("close", ("lastPrice", "closePrice"), Decimal("0.02")))
REPORT_ONLY = (("close_vs_official_close", "close", "closePrice"), ("volume", "volume", "volume"))
MATCH, MINOR, DISCREPANCY, UNVERIFIED = "MATCH", "MINOR_DIFFERENCE", "DISCREPANCY", "UNVERIFIED"


class CrossCheckDiscrepancy(RuntimeError):
    def __init__(self, symbol: str, detail: dict):
        super().__init__(f"CROSS_CHECK_DISCREPANCY:{symbol}")
        self.symbol = symbol
        self.detail = detail


def _decimal(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
    return number if number.is_finite() and number > 0 else None


def load_completed_session(evidence_root: Path | None, session: date) -> dict | None:
    """Rows by ISIN from the latest usable capture for ``session``; None if none."""
    if evidence_root is None:
        return None
    for directory in sorted(glob.glob(str(Path(evidence_root) / session.isoformat() / "*")), reverse=True):
        try:
            manifest = json.loads((Path(directory) / "MANIFEST.json").read_text())
            gate = manifest["session_gate"]
            if (manifest.get("market_status", "").lower() != "closed"
                    or gate.get("verdict") != "COMPLETED_SESSION"
                    or gate.get("session_date") != session.isoformat()):
                continue
            rows = {}
            for page in sorted(Path(directory).glob("market-watch-page-*.json")):
                for row in json.loads(page.read_text())["data"]["data"]:
                    rows[row.get("isin")] = row
            if len(rows) == manifest.get("rows"):
                return rows
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def compare(bar: dict | None, official: dict | None, *, tolerance: Decimal) -> tuple[str, dict]:
    if bar is None or official is None:
        return UNVERIFIED, {}
    differences, material = {}, False
    for ours, candidates, threshold in MATERIAL:
        theirs = next((name for name in candidates if _decimal(official.get(name)) is not None), None)
        a = _decimal(bar.get(ours))
        b = _decimal(official.get(theirs)) if theirs else None
        if a is None or b is None:
            return UNVERIFIED, {"field": ours}
        relative = abs(a - b) / b
        if relative > tolerance:
            differences[ours] = {"provider": str(a), "official": str(b), "official_field": theirs,
                                 "relative": str(round(relative, 6)), "material": relative > threshold}
            material = material or relative > threshold
    for label, ours, theirs in REPORT_ONLY:
        a, b = _decimal(bar.get(ours)), _decimal(official.get(theirs))
        if a is not None and b is not None and abs(a - b) / b > tolerance:
            differences[label] = {"provider": str(a), "official": str(b),
                                  "relative": str(round(abs(a - b) / b, 6)), "material": False}
    if material:
        return DISCREPANCY, differences
    return (MINOR, differences) if differences else (MATCH, {})


@dataclass
class CrossCheckedProvider:
    """Wrap a daily provider; quarantine symbols whose session-D bar disagrees."""

    provider: object
    session: date
    official_by_isin: dict | None
    isin_by_symbol: dict
    quarantine_dir: Path
    tolerance: Decimal = Decimal("0.005")

    def __post_init__(self):
        self.results: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return self.provider.name

    def fetch_daily_bars(self, *, symbol, start_date, end_date):
        response = self.provider.fetch_daily_bars(symbol=symbol, start_date=start_date, end_date=end_date)
        rows = json.loads(response.payload)
        bar = next((row for row in rows if row.get("date") == self.session.isoformat()), None)
        isin = self.isin_by_symbol.get(symbol)
        official = (self.official_by_isin or {}).get(isin) if isin else None
        verdict, detail = compare(bar, official, tolerance=self.tolerance)
        self.results[symbol] = {"verdict": verdict, "isin": isin, **({"differences": detail} if detail else {})}
        if verdict == DISCREPANCY:
            self.quarantine_dir.mkdir(parents=True, exist_ok=True)
            target = self.quarantine_dir / f"{symbol}-{self.session.isoformat()}.json"
            target.write_text(json.dumps({
                "symbol": symbol, "isin": isin, "session": self.session.isoformat(),
                "differences": detail, "source_uri": response.source_uri,
                "provider_payload": rows}, indent=2, sort_keys=True) + "\n")
            raise CrossCheckDiscrepancy(symbol, detail)
        return response

    def __getattr__(self, name):
        if name in ("provider", "results") or name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.provider, name)
