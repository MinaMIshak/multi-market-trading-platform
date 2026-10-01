"""Fetch one TradingView daily series as the raw websocket stream (helper process).

Runs under the dedicated TradingView tooling interpreter, which has
``tvDatafeed`` installed, so the platform interpreter needs no new
dependency:

    <tradingview-venv>/bin/python tools/tradingview_fetch.py EGX:COMI 700

It prints one JSON object: ``{"symbol", "n_bars", "adjustment", "raw"}``.
``raw`` is the unmodified websocket text (``~m~`` frames) up to
``series_completed``. Parsing, dating (Africa/Cairo) and validation happen in
the platform (app/data/providers/tradingview.py), never here. Anonymous,
unauthenticated access only: no login and no token. Exit 1 on error.
"""
import json
import sys


def main(argv):
    if len(argv) != 2 or ":" not in argv[0] or not argv[1].isdigit():
        print(json.dumps({"error": "usage: EXCHANGE:SYMBOL N_BARS"}))
        return 1
    symbol, n_bars = argv[0], int(argv[1])
    if not 1 <= n_bars <= 5000:
        print(json.dumps({"error": "n_bars out of range"}))
        return 1
    from tvDatafeed import Interval, TvDatafeed

    captured = {}

    def capture(raw_data, _symbol):
        captured["raw"] = raw_data
        return None

    # Keep the library's protocol; replace only its DataFrame parser with capture.
    TvDatafeed._TvDatafeed__create_df = staticmethod(capture)
    client = TvDatafeed()
    try:
        client.get_hist(symbol=symbol, exchange=symbol.split(":")[0],
                        interval=Interval.in_daily, n_bars=n_bars)
    except Exception as exc:  # network/protocol; never echo payloads
        print(json.dumps({"error": type(exc).__name__}))
        return 1
    finally:
        try:
            client.ws.close()
        except Exception:
            pass
    raw = captured.get("raw") or ""
    if "series_completed" not in raw:
        print(json.dumps({"error": "series_not_completed"}))
        return 1
    print(json.dumps({"symbol": symbol, "n_bars": n_bars, "adjustment": "splits", "raw": raw}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
