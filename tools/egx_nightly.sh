#!/bin/sh
# EGX daily pre-market chain (Paper/Shadow only; LIVE_MONEY=DISABLED).
#
# Run from a pinned release directory (tools/preview_release.py) as egx-agent,
# before the EGX open (cron 08:30 Africa/Cairo, Sun-Thu). The previous
# session is complete, and the official post-close capture was taken at 16:45
# by a separate cron entry (app.data.egx_market_watch_capture). Each run has
# its own acquisition date, so immutable artifacts never collide.
#   1. official-index / calendar maintenance (previous session verified)
#   2. TradingView daily acquisition + official cross-check (EGX-XCHECK-v2)
#   3. EGX-RANK-v1 ranking, system Paper/Shadow candidates (entry today), lifecycles
#   4. legacy universe classification scan history
#   5. macro context (public FRED CSV; context only, never a candidate input)
#   6. verified runtime snapshot publication (pointer switch)
# Steps run in order. Every exit code is logged; a failed step never fabricates
# downstream evidence (later steps read whatever is verified). One run at a time.
set -u
STATE=${EGX_STATE:-/home/egx-agent/er1-autopilot/state}
DB=${EGX_OPERATIONAL_DB:-/home/egx-agent/research-data/paper-shadow-operational/platform.db}
DATA=${EGX_OPERATIONAL_DATA:-/home/egx-agent/research-data/paper-shadow-operational/data}
PY=${EGX_PYTHON:-/home/egx-agent/work/egx-trading-platform-us/.venv/bin/python}
TVPY=${EGX_TV_PYTHON:-/home/egx-agent/research-data/paper-shadow-operational/tooling/tradingview-venv/bin/python}
REVISION=${EGX_BUILD_REVISION:-unknown}
LOGDIR="$STATE/nightly"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/$(TZ=Africa/Cairo date +%Y%m%d).log"
exec 9>"$LOGDIR/nightly.lock"
if ! flock -n 9; then
  echo "$(date -u +%FT%TZ) LOCKED" >> "$LOG"
  exit 75
fi
step() {
  name=$1; shift
  echo "$(date -u +%FT%TZ) START $name" >> "$LOG"
  "$@" >> "$LOG" 2>&1
  code=$?
  echo "$(date -u +%FT%TZ) END $name exit=$code" >> "$LOG"
  return 0
}
step calendar_maintenance "$PY" -m app.data.official_calendar_maintenance --db-path "$DB" --data-root "$DATA" \
  --state-dir "$STATE/egx-calendar-maintenance"
step tradingview_daily "$PY" -m app.data.tradingview_refresh --db-path "$DB" --data-root "$DATA" \
  --state-dir "$STATE/tradingview" --tv-python "$TVPY" --market-watch-evidence "$STATE/market-watch-evidence"
step ranking "$PY" -m app.egx_ranking_run --db-path "$DB" --data-root "$DATA" \
  --report "$STATE/egx-ranking/egx-ranking.json" --candidates "$STATE/egx-ranking/system-candidates.jsonl"
step universe_scan "$PY" -m app.egx_universe_scan --db-path "$DB" --history-path "$STATE/egx-scan/egx-scan-history.json"
step macro "$PY" -m app.research.macro_fetch --data-root "$DATA" --report "$STATE/macro/macro-context.json"
step publish "$PY" tools/publish_runtime_snapshot.py publish --db "$DB" --snapshots-root "$STATE/snapshots" \
  --pointer "$STATE/public-bundle.json" --build-revision "$REVISION" \
  --scan-history "$STATE/egx-scan/egx-scan-history.json" \
  --calendar-maintenance-status "$STATE/egx-calendar-maintenance/last-run.json" \
  --ranking-report "$STATE/egx-ranking/egx-ranking.json" \
  --macro-report "$STATE/macro/macro-context.json"
echo "$(date -u +%FT%TZ) CHAIN_DONE live_money=DISABLED" >> "$LOG"
