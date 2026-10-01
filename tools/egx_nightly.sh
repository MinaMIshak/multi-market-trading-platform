#!/bin/sh
# EGX nightly chain (Paper/Shadow only; LIVE_MONEY=DISABLED).
#
# Run from a pinned release directory (tools/preview_release.py) as egx-agent,
# after the EGX session has completed (cron 18:10 Africa/Cairo):
#   1. official market-watch post-close capture (verification evidence only)
#   2. official-index / calendar maintenance (verified sessions)
#   3. TradingView daily acquisition + official cross-check + quarantine
#   4. EGX-RANK-v1 ranking, system Paper/Shadow candidates, lifecycles
#   5. legacy universe classification scan history
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
step market_watch_capture "$PY" -m app.data.egx_market_watch_capture --out-root "$STATE/market-watch-evidence"
step calendar_maintenance "$PY" -m app.data.official_calendar_maintenance --db-path "$DB" --data-root "$DATA" \
  --state-dir "$STATE/egx-calendar-maintenance"
step tradingview_daily "$PY" -m app.data.tradingview_refresh --db-path "$DB" --data-root "$DATA" \
  --state-dir "$STATE/tradingview" --tv-python "$TVPY" --market-watch-evidence "$STATE/market-watch-evidence"
step ranking "$PY" -m app.egx_ranking_run --db-path "$DB" --data-root "$DATA" \
  --report "$STATE/egx-ranking/egx-ranking.json" --candidates "$STATE/egx-ranking/system-candidates.jsonl"
step universe_scan "$PY" -m app.egx_universe_scan --db-path "$DB" --history-path "$STATE/egx-scan/egx-scan-history.json"
step publish "$PY" tools/publish_runtime_snapshot.py publish --db "$DB" --snapshots-root "$STATE/snapshots" \
  --pointer "$STATE/public-bundle.json" --build-revision "$REVISION" \
  --scan-history "$STATE/egx-scan/egx-scan-history.json" \
  --calendar-maintenance-status "$STATE/egx-calendar-maintenance/last-run.json" \
  --ranking-report "$STATE/egx-ranking/egx-ranking.json"
echo "$(date -u +%FT%TZ) CHAIN_DONE live_money=DISABLED" >> "$LOG"
