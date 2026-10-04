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
  bar for D. This is not a failure and not a confirmation;
- UNAVAILABLE_STALE_SECONDARY: the official row exists but is not an
  observation of session D (``row_observation`` status other than
  SESSION_ALIGNED). No price comparison is made across sessions, and the
  primary bar's admission is unaffected.

Session alignment is decided per row from the capture's own evidence
(app/data/providers/egx_market_watch.py, ``row_observation``), never from the
folder name, the capture time, the market status or ``writeTime`` alone.
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
STALE_SECONDARY = "UNAVAILABLE_STALE_SECONDARY"


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


@dataclass
class SessionEvidence:
    """Official evidence for primary session D, split into session-aligned and non-aligned rows."""

    session: date
    status: str                      # AVAILABLE / UNAVAILABLE_STALE_SECONDARY / UNAVAILABLE_NO_SECONDARY
    rows: dict                       # ISIN -> official row observed in session D
    not_aligned: dict                # ISIN -> row_observation (why it is not session D)
    capture: dict | None = None      # capture-level times and distributions

    def summary(self) -> dict:
        from collections import Counter
        return {"primary_session": self.session.isoformat(), "secondary_status": self.status,
                "aligned_rows": len(self.rows),
                "not_aligned": dict(Counter(o["status"] for o in self.not_aligned.values())),
                **({"capture": self.capture} if self.capture else {})}


def load_session_evidence(evidence_root: Path | None, session: date) -> SessionEvidence:
    """Per-row session alignment from the latest complete capture taken on ``session``."""
    from collections import Counter
    from datetime import datetime
    from app.data.providers.egx_market_watch import SESSION_ALIGNED, row_observation
    empty = SessionEvidence(session, "UNAVAILABLE_NO_SECONDARY", {}, {})
    if evidence_root is None:
        return empty
    for directory in sorted(glob.glob(str(Path(evidence_root) / session.isoformat() / "*")), reverse=True):
        try:
            manifest = json.loads((Path(directory) / "MANIFEST.json").read_text())
            status = json.loads((Path(directory) / "market-status.json").read_text())["data"]
            rows = []
            for page in sorted(Path(directory).glob("market-watch-page-*.json")):
                rows.extend(json.loads(page.read_text())["data"]["data"])
            if len(rows) != manifest.get("rows"):
                continue
            status_day = datetime.fromisoformat(status["statusDate"]).date()
            captured_at = datetime.fromisoformat(manifest["captured_at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        closed = str(status.get("status", "")).strip().lower() == "closed"
        aligned, not_aligned = {}, {}
        for row in rows:
            observation = row_observation(row, status_date=status_day, captured_at=captured_at, market_closed=closed)
            if observation["status"] == SESSION_ALIGNED and observation["observation_session_date"] == session.isoformat():
                aligned[row.get("isin")] = row
            else:
                not_aligned[row.get("isin")] = observation
        capture = {"directory": directory, "capture_timestamp": captured_at.isoformat(),
                   "market_status": status.get("status"), "market_status_timestamp": status.get("statusDate"),
                   "source_write_dates": dict(Counter(str(r.get("writeTime"))[:8] for r in rows)),
                   "provider_last_trade_dates": dict(Counter(str(r.get("lastTradeDate"))[:10] for r in rows))}
        return SessionEvidence(session, "AVAILABLE" if aligned else STALE_SECONDARY, aligned, not_aligned, capture)
    return empty


def load_completed_session(evidence_root: Path | None, session: date) -> dict | None:
    """Session-aligned official rows by ISIN for ``session``; None if none are aligned."""
    evidence = load_session_evidence(evidence_root, session)
    return evidence.rows or None


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
    not_aligned_by_isin: dict | None = None

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
        stale = official is None and isin is not None and isin in (self.not_aligned_by_isin or {})
        if stale and bar is not None:
            # Never compare prices from different sessions; the primary bar's admission is unaffected.
            self.results[symbol] = {"verdict": STALE_SECONDARY, "isin": isin,
                                    "secondary_observation": self.not_aligned_by_isin[isin]}
            return response
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
