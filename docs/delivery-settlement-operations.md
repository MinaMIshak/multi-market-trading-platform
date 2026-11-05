# Settlement inventory and recovery

The host publication now includes `settlement_backlog`: a bounded snapshot of
receiver pending envelope count and exact bytes, taken under supervisor, delivery,
and spool leases in that order. SYSTEM displays the sanitized snapshot and its UTC
observation time. The API does not read private state. Journal metadata cannot
supply these counters. Private paths, envelope text and raw errors are excluded.

`INVENTORIED` means only that protected pending files were readable within the
128-envelope / 8 MiB limits. It does not validate JSON, reconcile conflicting event
identities, prove publication or settlement, inspect caller outboxes, certify
archive integrity, establish current health or authorize delivery. `UNAVAILABLE`
means the bounded inventory could not complete; counts are withheld. Missing
telemetry remains UNKNOWN. Zero pending envelopes is not a release acceptance gate.

Publication precedes archive movement. A successful settlement may therefore
publish a nonzero snapshot; the next host publication refreshes it. An archive
failure leaves the envelope pending and visible. Readers must treat the timestamp
as a historical observation. No polling service or host installation is supplied
by this repository change.

## Host recovery boundary

Use the existing pinned completion retry / settlement operation after operation
leases unwind. Retry settlement, not the completed delivery or writer operation.
A `PublicationFailure` must preserve its completed operation result and error;
an installed supervisor must not infer that the underlying operation failed or
rerun it solely because publication failed.

For conflicting or damaged legacy evidence, stop automated settlement of that
batch and preserve exact original bytes, filenames, digests, receipts and sidecar.
Do not delete files, edit timestamps, mint replacement event identities or choose
one conflicting event based on recency. Caller outbox and receiver evidence are
separate records and must both remain available for provenance reconciliation.
A repair executor is not implemented or authorized by this inventory feature.
The next implementation must require a protected, digest-bound repair manifest,
independent originating-job evidence, exclusive leases, durable before/after
records and restart-safe recovery before any mutation. Ambiguous evidence remains
fail-closed. Archive retention and disk provisioning remain host responsibilities.
