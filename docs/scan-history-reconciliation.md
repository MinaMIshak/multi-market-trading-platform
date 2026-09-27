# Historical scheduler completion reconciliation

The API and SYSTEM history reader optionally accept `EGX_SCAN_LEDGER_PATH`, an
absolute path to the existing scheduler SQLite database. Configure this only
when the history and ledger belong to the same deployment. It is disabled when
unset; it does not use a database URL, initialize storage, or read secrets.
Repository Compose passes this optional variable to the API, defaulting to empty
(disabled). For that template, the scheduler's `EGX_DB_PATH` and both services'
shared data volume establish `/app/data/platform.db` as the matching ledger path.
Set `EGX_SCAN_LEDGER_PATH=/app/data/platform.db` only for this matching deployment.
This is repository wiring only; deployment and runtime validation remain pending.

For a validated schema-v3 RUNNING history snapshot, the reader opens the ledger
with SQLite `mode=ro` and `query_only=ON`. A single bounded query must return one
VERIFIED_TRADING_DAY row matching market date, checkpoint, attempt count, and
start timestamp. Only SUCCEEDED with a timezone-aware finish at or after the
history classification timestamp and no later than the read time is reported.
New attempts cannot certify an older history snapshot. Missing, locked, corrupt,
ambiguous, mismatched or invalid ledger evidence leaves completion unknown.
Relative paths and symlink paths are rejected. The SQLite read reflects a point
in time; it is not a continuing assertion about the latest scheduler state.

The loaded history retains its original RUNNING attempt and classification time.
`reconciled_scheduler_completion` separately carries the ledger result; product
reporting revalidates the binding before exposing `scheduler_completion`.
The reader adds `observed_at` after reading history and the optional ledger.
Product/API `completion_evidence` reports `origin` as `RECORDED_HISTORY` or
`READ_ONLY_LEDGER`, derived from the admitted completion branch, plus that read
timestamp as `observed_at`. The timestamp must be timezone-aware, at or after
both classification and scheduler finish, and no later than validation time.
Missing or invalid observation time suppresses completion and its evidence while
preserving valid historical classification. Unknown and legacy completion use
null evidence. LIVE distinguishes recorded from ledger-reconciled success and
labels observation time separately from classification time. Read time is not
completion time or evidence of current worker health; an older valid observation
remains explicitly historical. No storage paths are exposed in this metadata.
Neither history nor ledger is rewritten. Previously recorded SUCCEEDED history
remains historical evidence without requiring a live ledger. Legacy v1/v2
history cannot acquire scheduler completion through reconciliation.

This result certifies a dated explicit-scope classification only. It does not
establish current readiness, universe coverage, current worker health, fills or
performance. U.S. scheduler history remains unconnected.

Offline validation:

```sh
PYTHONPATH=.:tests python3 -m unittest test_scan_attempt_contract test_product_scan_history_contract test_egx_scan_history test_egx_scan_dispatch test_product_shell_contract
```

Fixtures exercise actual SQLite persistence and a captured pre-completion
history snapshot; they are not market or deployed-runtime evidence.
