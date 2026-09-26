# Opt-in local EGX scan scheduling

The worker defaults to `EGX_SCAN_MODE=disabled`; the existing OBSERVE provider
mode remains unchanged. `EGX_SCAN_MODE=local` explicitly enables local scan
verification independently of paid/provider refresh. No acquisition is performed.
Set absolute `EGX_SCAN_CONFIG_PATH` and `EGX_SCAN_HISTORY_PATH` paths in the worker
runtime. Data is read from the existing database's parent directory. Compose
already shares the history path with the API; activation requires explicit
worker environment configuration. Do not activate until integration validation.

The opt-in policy adds 18:30 and 19:00 Africa/Cairo checkpoints, each with a
15-minute start window and mandatory verified-trading-day truth. The existing
orchestrator and durable ledger govern claims and stale-job recovery. Only
PENDING work is dispatched. Failure is not retried on every poll; the fallback
is suppressed after primary completion. Configuration is reloaded per attempt.

Completion means the entire explicit scope was classified and history persisted.
It does not mean all symbols passed verification. Evidence-blocked symbols remain
blocked, and their classification does not trigger the fallback. Infrastructure
or configuration exceptions mark the job failed with only the exception type.
The verifier retains all existing admission and freshness gates and does not
publish candidates or infer fills. Scope is not authoritative universe evidence.

Validation: mocked dispatcher and existing coordinator/configuration/history
regressions pass. Actual worker/SQLite/decoder/verifier integration remains
pending unavailable runtime dependencies. No scheduled runtime run or deployment
has been demonstrated. Existing five-symbol launch restrictions still apply.
