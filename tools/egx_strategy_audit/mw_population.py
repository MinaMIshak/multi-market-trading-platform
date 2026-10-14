"""Population check: official market-watch rows vs stored TradingView bars, by candidate session."""
import collections
import glob
import json
import sys
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.data.validated_daily_repository import ValidatedDailyArtifactRepository  # noqa: E402

repo = ValidatedDailyArtifactRepository(database_path="/home/egx-agent/research-data/paper-shadow-operational/platform.db",
                                        data_root="/home/egx-agent/research-data/paper-shadow-operational/data")
latest = repo.latest_by_symbol("tradingview_tvdatafeed_egx")
bars_by_isin = {}
for ticker, artifact in latest.items():
    bars_by_isin[artifact["isin"]] = {b["date"]: b for b in repo.load(artifact).bars}

for folder in sorted(glob.glob("/home/egx-agent/er1-autopilot/state/market-watch-evidence/*/*")):
    rows = []
    for page in sorted(glob.glob(folder + "/market-watch-page-*.json")):
        rows += json.load(open(page))["data"]["data"]
    status = json.load(open(folder + "/market-status.json"))["data"]
    write_dates = collections.Counter(str(r.get("writeTime"))[:8] for r in rows)
    result = collections.Counter()
    zero_trades = []
    for row in rows:
        bars = bars_by_isin.get(row.get("isin"))
        if not bars:
            result["NO_PRIMARY_SERIES"] += 1
            continue
        write_day = f"{str(row['writeTime'])[:4]}-{str(row['writeTime'])[4:6]}-{str(row['writeTime'])[6:8]}"
        prev_day = str(row["lastTradeDate"])[:10]
        if not row.get("trades"):
            zero_trades.append((row.get("reuters"), prev_day, row.get("volume")))
        for label, day in (("write", write_day), ("lastTradeDate", prev_day)):
            bar = bars.get(day)
            if bar is None:
                result[f"{label}:no_primary_bar"] += 1
                continue
            same_volume = Decimal(str(row["volume"])) == bar["volume"]
            close = Decimal(str(row["lastPrice"] or row["closePrice"]))
            close_ok = abs(close - bar["close"]) / bar["close"] <= Decimal("0.005")
            result[f"{label}:volume_equal={same_volume}"] += 1
            result[f"{label}:close_within_0.5%={close_ok}"] += 1
        prev_bar = bars.get(prev_day)
        if prev_bar is not None:
            prev_ok = abs(Decimal(str(row["prevClose"])) - prev_bar["close"]) / prev_bar["close"] <= Decimal("0.01")
            result[f"prevClose_matches_primary_close_on_lastTradeDate={prev_ok}"] += 1
    print(folder.split("evidence/")[1], status.get("status"), status.get("statusDate"), "rows", len(rows),
          "writeDates", dict(write_dates))
    for key in sorted(result):
        print("   ", key, result[key])
    print("    zero-trade rows:", zero_trades[:6], len(zero_trades))
