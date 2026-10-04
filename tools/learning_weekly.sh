#!/bin/sh
# Weekly walk-forward evaluation of the forecast champion and challengers (research only; LIVE_MONEY=DISABLED).
#
# cron 10:00 Africa/Cairo on Saturday (no EGX or US session), from a pinned release directory as egx-agent.
# Writes <state>/learning/<market>/walkforward.json; the daily learning step reads it. It never promotes a model.
set -u
STATE=${EGX_STATE:-/home/egx-agent/er1-autopilot/state}
DB=${EGX_OPERATIONAL_DB:-/home/egx-agent/research-data/paper-shadow-operational/platform.db}
DATA=${EGX_OPERATIONAL_DATA:-/home/egx-agent/research-data/paper-shadow-operational/data}
USDATA=${EGX_US_DATA:-/home/egx-agent/research-data/us-paper-shadow/data}
PY=${EGX_PYTHON:-/home/egx-agent/work/egx-trading-platform-us/.venv/bin/python}
REVISION=${EGX_BUILD_REVISION:-unknown}
LOGDIR="$STATE/learning-weekly"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/$(TZ=Africa/Cairo date +%Y%m%d).log"
exec 9>"$LOGDIR/learning-weekly.lock"
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
step walkforward_egx "$PY" -m app.learning.walkforward_run --state "$STATE/learning" --market EGX \
  --egx-db "$DB" --egx-data "$DATA" --egx-evidence "$STATE/market-watch-evidence" --build-revision "$REVISION"
step walkforward_us "$PY" -m app.learning.walkforward_run --state "$STATE/learning" --market US \
  --us-data "$USDATA" --us-report "$STATE/us/us-ranking.json" --build-revision "$REVISION"
echo "$(date -u +%FT%TZ) WEEKLY_DONE live_money=DISABLED" >> "$LOG"
