# Scheduler visibility

Set `EGX_SCHEDULER_HEARTBEAT_PATH` to the same absolute shared file path in
worker and API environments. The parent directory must already exist and be
writable by the worker; API access can be read-only. Configuration is optional
and is not yet wired into deployment.

After each completed poll the EGX worker atomically replaces this local JSON
file. SYSTEM reports RECENT_POLL until three poll intervals (at least 60 seconds)
have elapsed, then STALE. Missing, invalid or future-dated evidence reports
UNKNOWN. A stopped/crashed worker's last heartbeat expires naturally. This is
local trusted-process evidence, not an authenticated remote heartbeat.

The mode remains explicit (`observe` or `paper_refresh`). A recent poll proves
neither successful jobs nor scans, source health or U.S. scheduling. The existing
job ledger remains the source of job outcomes. Heartbeat write failures surface
as worker errors rather than silently reporting healthy operation.

Validation: `python3 -m unittest tests.test_scheduler_heartbeat tests.test_system_visibility -v`.
Worker/FastAPI integration and deployment validation remain pending dependencies
and an authorized safe deployment mechanism.
