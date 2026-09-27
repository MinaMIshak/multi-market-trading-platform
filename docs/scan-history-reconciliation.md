# Historical scheduler completion reconciliation

The API and SYSTEM history reader optionally accept `EGX_SCAN_LEDGER_PATH`, an
absolute path to the existing scheduler SQLite database. Configure this only
when the history and ledger belong to the same deployment. It is disabled when
unset; it does not use a database URL, initialize storage, or read secrets.
No deployment configuration is enabled by this change.

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
