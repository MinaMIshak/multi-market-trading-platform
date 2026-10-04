#!/bin/sh
# US daily chain (Paper/Shadow only; LIVE_MONEY=DISABLED).
#
# Run from a pinned release directory as egx-agent after the NYSE close:
# cron 06:15 Africa/Cairo, Tuesday-Saturday (processing the Monday-Friday
# session that closed overnight). US data and state are separate from EGX.
#   1. US universe, NYSE session verification, daily acquisition, cross-check,
#      US-RANK-v1 ranking, US Paper/Shadow candidates and lifecycles (app.us_run)
#   2. research context refresh (app.context.run)
#   3. verified runtime snapshot publication with every report (shared publish lock)
# Every exit code is logged; a failed step never fabricates downstream evidence.
set -u
STATE=${EGX_STATE:-/home/egx-agent/er1-autopilot/state}
DB=${EGX_OPERATIONAL_DB:-/home/egx-agent/research-data/paper-shadow-operational/platform.db}
DATA=${EGX_OPERATIONAL_DATA:-/home/egx-agent/research-data/paper-shadow-operational/data}
USDATA=${EGX_US_DATA:-/home/egx-agent/research-data/us-paper-shadow/data}
PY=${EGX_PYTHON:-/home/egx-agent/work/egx-trading-platform-us/.venv/bin/python}
TVPY=${EGX_TV_PYTHON:-/home/egx-agent/research-data/paper-shadow-operational/tooling/tradingview-venv/bin/python}
REVISION=${EGX_BUILD_REVISION:-unknown}
LOGDIR="$STATE/us-nightly"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/$(TZ=Africa/Cairo date +%Y%m%d).log"
exec 9>"$LOGDIR/us-nightly.lock"
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
step us_run "$PY" -m app.us_run --data-root "$USDATA" --state "$STATE/us" --tv-python "$TVPY" --size 500
step context "$PY" -m app.context.run --data-root "$DATA" --report "$STATE/context/context-report.json" \
  --macro-report "$STATE/macro/macro-context.json" --tv-python "$TVPY"
step learning "$PY" -m app.learning.daily --state "$STATE/learning" --report "$STATE/learning/learning-report.json" \
  --egx-db "$DB" --egx-data "$DATA" --egx-evidence "$STATE/market-watch-evidence" \
  --egx-ranking-report "$STATE/egx-ranking/egx-ranking.json" --snapshots-root "$STATE/snapshots" \
  --us-data "${EGX_US_DATA:-/home/egx-agent/research-data/us-paper-shadow/data}" --us-report "$STATE/us/us-ranking.json" \
  --context-report "$STATE/context/context-report.json" --build-revision "$REVISION"
step publish flock "$STATE/publish.lock" "$PY" tools/publish_runtime_snapshot.py publish --db "$DB" \
  --snapshots-root "$STATE/snapshots" --pointer "$STATE/public-bundle.json" --build-revision "$REVISION" \
  --scan-history "$STATE/egx-scan/egx-scan-history.json" \
  --calendar-maintenance-status "$STATE/egx-calendar-maintenance/last-run.json" \
  --ranking-report "$STATE/egx-ranking/egx-ranking.json" \
  --macro-report "$STATE/macro/macro-context.json" \
  --context-report "$STATE/context/context-report.json" \
  --us-ranking-report "$STATE/us/us-ranking.json" \
  --egx-experiment-report "$STATE/egx-experiment/egx-experiment.json" \
  --learning-report "$STATE/learning/learning-report.json"
echo "$(date -u +%FT%TZ) CHAIN_DONE live_money=DISABLED" >> "$LOG"
