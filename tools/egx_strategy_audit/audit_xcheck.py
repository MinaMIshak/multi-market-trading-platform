"""Per-session cross-check alignment from chain logs, plus the 10-08 capture."""
import glob
import json

STATE = "/home/egx-agent/er1-autopilot/state"
for path in sorted(glob.glob(f"{STATE}/nightly/2026*.log")):
    lines = open(path).read().splitlines()
    for k, line in enumerate(lines):
        if line.endswith("START tradingview_daily") and k + 1 < len(lines):
            try:
                d = json.loads(lines[k + 1])
            except ValueError:
                continue
            c = d.get("cross_check_secondary") or {}
            cap = c.get("capture") or {}
            print(path.rsplit("/", 1)[1], d.get("session"), d.get("cross_check"), c.get("secondary_status"),
                  c.get("aligned_rows"), c.get("not_aligned"), cap.get("market_status_timestamp"),
                  cap.get("source_write_dates"), cap.get("provider_last_trade_dates"))
for manifest in sorted(glob.glob(f"{STATE}/market-watch-evidence/2026-10-08/*/MANIFEST.json")):
    m = json.load(open(manifest))
    print("10-08 capture:", m.get("captured_at"), m.get("market_status"), m.get("market_status_date"),
          m.get("observation_times"), m.get("session_gate"))
