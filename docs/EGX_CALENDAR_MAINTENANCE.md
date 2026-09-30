# Daily EGX official-index and calendar maintenance

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED. This job keeps official session
evidence current for the operational database, so that daily freshness is
decided from verified sessions (`CURRENT`/`STALE`) rather than staying
`UNKNOWN`. It produces calendar evidence only. It does not establish exchange
membership or source entitlement, and it produces no candidates or signals.

## What one run does

`python -m app.data.official_calendar_maintenance` (module
`app/data/official_calendar_maintenance.py`):

1. Refuses to start unless `PRAGMA integrity_check` is `ok`.
2. Computes the last *completed* session date in Africa/Cairo: today only
   from 16:00 Cairo (EGX trading ends 14:30), otherwise yesterday. Today's open
   session is never fetched or backfilled.
3. Fetches only after the newest official bar already admitted for every
   calendar index (CASE30, EGX70_EWI, EGX100_EWI). If that is past the last
   completed session, nothing is fetched (`UP_TO_DATE`).
4. Probes all three indices first. On a network or provider error, or if the
   indices disagree, it writes nothing and exits 1. If no index has bars
   (weekend or holiday), it fetches nothing (`NO_NEW_SESSIONS`).
5. Otherwise it runs the reviewed `egx_official_public` refresh job, with
   `snapshot_date` set to the Cairo date at acquisition (never pinned):
   `ADMITTED_NEW_SESSIONS`.
6. Runs the offline calendar backfill over the trailing 14 days up to the last
   completed session. It is deterministic: `VERIFIED` only from admitted
   official bars (`SAME_DAY_OFFICIAL`/`HISTORICAL_OFFICIAL`), `WEEKEND` by
   rule, and everything else stays `UNKNOWN`.
7. Re-checks integrity and fails if any lifecycle table changed (candidates,
   signals, positions, trade_plans, trade_outcomes, risk_decisions).

Holds a non-blocking lock. An overlapping run exits 75 and writes nothing.
Exit codes: 0 success or no-op, 1 failure, 75 locked.

## Paths

| Item | Path |
|---|---|
| Runtime (pinned) | `/home/egx-agent/er1-autopilot/state/releases/<full-sha>` (export with `tools/preview_release.py`) |
| Python | `/home/egx-agent/work/egx-trading-platform-us/.venv/bin/python` |
| Database | `/home/egx-agent/research-data/paper-shadow-operational/platform.db` |
| Data root | `/home/egx-agent/research-data/paper-shadow-operational/data` |
| State dir | `/home/egx-agent/er1-autopilot/state/egx-calendar-maintenance` |
| Lock | `<state dir>/egx-calendar-maintenance.lock` |
| Last outcome | `<state dir>/last-run.json`, `<state dir>/last-success.json` |
| JSON log | `<state dir>/logs/egx-calendar-maintenance-YYYYMM.jsonl` (one record per run, no secrets) |
| Cron stdout/stderr | `<state dir>/logs/cron.log` |

## Schedule

The host timezone is `Africa/Cairo` (`/etc/timezone`). The job runs daily as
`egx-agent` at 18:17 Cairo, after the close and after official index
publication. It also runs on weekends: they end as a no-op, and after a holiday
the next run catches up. The module enforces the Cairo completion cutoff itself,
so a mis-set clock or a manual run during the session cannot admit the open
session.

Crontab entry (user crontab of `egx-agent`, no root). `<sha>` is the full commit
of the pinned release:

```
17 18 * * * cd /home/egx-agent/er1-autopilot/state/releases/<sha> && env -i HOME=/home/egx-agent PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 TZ=Africa/Cairo EGX_BUILD_REVISION=<sha> /home/egx-agent/work/egx-trading-platform-us/.venv/bin/python -m app.data.official_calendar_maintenance --db-path /home/egx-agent/research-data/paper-shadow-operational/platform.db --data-root /home/egx-agent/research-data/paper-shadow-operational/data --state-dir /home/egx-agent/er1-autopilot/state/egx-calendar-maintenance >> /home/egx-agent/er1-autopilot/state/egx-calendar-maintenance/logs/cron.log 2>&1 # EGX_CALENDAR_MAINTENANCE
```

## Commands

Manual run: the same command as the crontab entry without the schedule
fields. It is safe to repeat.

Validate the last run and the database:

```
cat /home/egx-agent/er1-autopilot/state/egx-calendar-maintenance/last-run.json
tail -n 3 /home/egx-agent/er1-autopilot/state/egx-calendar-maintenance/logs/egx-calendar-maintenance-$(date +%Y%m).jsonl
python3 -c "import sqlite3;c=sqlite3.connect('file:/home/egx-agent/research-data/paper-shadow-operational/platform.db?mode=ro',uri=True);print(c.execute('pragma integrity_check').fetchone(), c.execute(\"select max(market_date) from market_sessions where status='VERIFIED'\").fetchone())"
crontab -l | grep EGX_CALENDAR_MAINTENANCE
```

Disable or remove, keeping every other crontab line:

```
crontab -l | grep -v '# EGX_CALENDAR_MAINTENANCE$' | crontab -
```

Move to a newer release: export it, run the manual command from it once,
then replace the entry (remove it as above, then add the new line).

## Observability and limits

- `last-run.json` / `last-success.json` record status, outcome, fetch and
  backfill ranges, the snapshot date, evidence-table counts before and after,
  and integrity. SYSTEM does not read these files yet. The runtime serves a
  snapshot bundle, and the bundle does not carry them.
- The job does not refresh the public snapshot bundle. The preview shows new
  sessions only after a new snapshot is taken and served
  (`docs/PREVIEW_RUNTIME.md`).
- The provider is the reviewed anonymous public `egx_official_public`
  source. An outage produces a FAILED record and no data. The next run catches
  up from the last admitted bar.
