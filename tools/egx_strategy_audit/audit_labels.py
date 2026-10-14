"""Explicit learning-label integrity checks on the real EGX dataset."""
import glob
import json
import sys
from pathlib import Path
from datetime import date, datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.learning import dataset, sources  # noqa: E402

series = sources.load_egx("/home/egx-agent/research-data/paper-shadow-operational/platform.db",
                          "/home/egx-agent/research-data/paper-shadow-operational/data",
                          "/home/egx-agent/er1-autopilot/state/market-watch-evidence")
closes = {s.ticker: {b[0]: b[4] for b in s.bars} for s in series}
ds = dataset.build(series)
results = {}
results["no_weekend_sessions"] = all(date.fromisoformat(d).weekday() not in (4, 5) for d in ds.sessions)
results["holiday_2026_10_08_not_a_session"] = "2026-10-08" not in ds.sessions
seen = set()
dup = 0
for i in range(ds.n):
    key = (ds.row_ticker[i], ds.row_session[i])
    dup += key in seen
    seen.add(key)
results["no_duplicate_rows"] = dup == 0
bad_order = bad_ret = bad_gap = 0
checked = 0
for i in range(ds.n):
    for h in (1, 2, 3, 5):
        if ds.status[h][i] != 0:
            continue
        k, end = ds.row_session[i], ds.end[h][i]
        if end != k + h:
            bad_order += 1
        t = ds.ticker(i)
        start, stop = ds.sessions[k], ds.sessions[end]
        expected = closes[t][stop] / closes[t][start] - 1
        checked += 1
        if abs(expected - ds.ret[h][i]) > 1e-9:
            bad_ret += 1
        if any(d not in closes[t] for d in ds.sessions[k + 1:end + 1]):
            bad_gap += 1
results["label_end_is_h_observed_sessions_later"] = bad_order == 0
results["label_return_equals_close_ratio"] = bad_ret == 0
results["no_label_across_missing_bars (no forward-fill)"] = bad_gap == 0
k07 = ds.session_index.get("2026-10-07")
i07 = next(i for i in range(ds.n) if ds.row_session[i] == k07)
results["label_after_2026-10-07_matures_on_2026-10-11"] = ds.sessions[ds.end[1][i07]] == "2026-10-11"
late = 0
for path in glob.glob("/home/egx-agent/er1-autopilot/state/learning/egx/forecasts/*.json"):
    doc = json.load(open(path))
    cutoff = datetime.fromisoformat(doc["forecast_cutoff"])
    feature_session = doc["feature_snapshot_session"]
    # The forecast is made after its feature session closed and before the next session's outcome.
    next_index = ds.session_index.get(feature_session, -1) + 1
    if not (cutoff.date() > date.fromisoformat(feature_session) or cutoff.hour >= 14):
        late += 1
    if next_index < len(ds.sessions) and cutoff.date() > date.fromisoformat(ds.sessions[next_index]):
        late += 1
results["frozen_forecasts_feature_session < cutoff < outcome"] = late == 0
print(json.dumps({"labels_checked": checked, "rows": ds.n, "sessions": [ds.sessions[0], ds.sessions[-1]],
                  "results": results}, indent=1))
