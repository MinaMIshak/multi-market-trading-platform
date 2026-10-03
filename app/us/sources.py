"""US daily source admission (separate from the EGX daily source registry).

The operator's 2026-10-03 mission authorised free/public or already-accessible
sources for this personal, non-commercial research platform. There is no
free official source of US consolidated daily OHLCV (exchanges license it),
so the primary is TradingView's anonymous chart feed (vendor ICE), the
same technical path already admitted for EGX. No contractual licence is
claimed. See docs/SOURCE_DECISION_MATRIX.md.
"""

US_DAILY_SOURCE = {
    "provider": "tradingview_tvdatafeed_us",
    "market": "US",
    "status": "ADMITTED",
    "entitlement": "OPERATOR_ACCEPTED_UNLICENSED",
    "licensing": "NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED",
    "basis": "operator mission 2026-10-03 (autonomous source selection; no paid tier)",
    "verification": "Yahoo Finance chart API sample (unofficial; report-only)",
    "delay": "end-of-day; completed NYSE sessions only",
}
