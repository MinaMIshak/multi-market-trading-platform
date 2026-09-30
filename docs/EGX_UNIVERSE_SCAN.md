# Daily EGX universe classification (scan history)

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED.

`python -m app.egx_universe_scan --db-path <platform.db> --history-path <egx-scan-history.json>`
runs the existing scan coordinator (`app/egx_scan.py`) over every EQUITY
ticker in the security master. The scope reference is
`security-master-equity-universe`, labelled
`EXPLICIT_SELECTION_NOT_AUTHORITATIVE_UNIVERSE`.

- No launch evidence is attached, so every symbol goes through the existing
  gates. While no daily source is admitted, each one is `EVIDENCE_BLOCKED` and
  `scanned=false`. Nothing is verified, published, ranked or filled.
- The operational database is only read; the test checks that its bytes are
  unchanged. The only output is the scan-history summary (schema 2, dated
  `completed_at`), which SYSTEM and the coverage breakdown read as a
  historical run. It is not current readiness.
- When a source is admitted and launch evidence exists, the same coordinator
  verifies those symbols through SWING; the others stay blocked.

## First run (2026-09-30)

312 requested, 0 scanned, 312 `EVIDENCE_BLOCKED`, 0 WATCH or READY_NO_SIGNAL.
Operational DB counts unchanged, integrity `ok`.
History: `/home/egx-agent/er1-autopilot/state/egx-scan/egx-scan-history.json`.

## Schedule

The egx-agent crontab runs it daily at 18:45 Cairo, after the calendar
maintenance job, from the same pinned release (marker `# EGX_UNIVERSE_SCAN`):

```
45 18 * * * cd /home/egx-agent/er1-autopilot/state/releases/<sha> && env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 TZ=Africa/Cairo /home/egx-agent/work/egx-trading-platform-us/.venv/bin/python -m app.egx_universe_scan --db-path /home/egx-agent/research-data/paper-shadow-operational/platform.db --history-path /home/egx-agent/er1-autopilot/state/egx-scan/egx-scan-history.json >> /home/egx-agent/er1-autopilot/state/egx-scan/cron.log 2>&1 # EGX_UNIVERSE_SCAN
```

Include the history in a snapshot with `tools/runtime_state_snapshot.py
--scan-history /home/egx-agent/er1-autopilot/state/egx-scan/egx-scan-history.json`.
Remove it with `crontab -l | grep -v '# EGX_UNIVERSE_SCAN$' | crontab -`.
