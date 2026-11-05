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

## Read-only repair planning

`tools.delivery_repair.plan_repair(publication, request_path, request_sha256)`
provides a host-private planning API, with no CLI, host dispatch or mutation
executor. A protected `delivery-repair-request-v1` JSON document must contain
exactly `schema`, `live_money: false`, `references` (receiver-relative filenames
mapped to SHA256 pins), and `originating_records` (absolute protected paths outside
the receiver state mapped to SHA256 pins). Both maps must contain 1–128 entries.
References permit completion envelopes, archived envelopes and observation
receipts only; traversal and symlinks are rejected by the protected readers.

Under supervisor, delivery and spool leases, planning binds every current pending
filename, exact bytes/digest of each referenced file, the sidecar or its absence,
and independent job records. The initial evidence inventory budget is 8 MiB; overlapping
references count each capture. Pin rechecks reread this bounded scope. Files are rechecked before returning a deterministic
plan and its SHA256. Malformed evidence can be hashed without being interpreted.
Errors return no plan and preserve original evidence. Existing lease files may be
created as part of locking; evidence files are never written.

The plan explicitly remains UNRESOLVED and does not authorize mutation. It covers
pending state, the sidecar and explicit references, **not all archive history or
caller outboxes**. Physical separation of job records does not prove their semantic
authority. A trusted host must supply and reconcile all relevant originating
records and preserve the returned plan in protected storage. This API is not yet
wired into the installed supervisor. A future executor still requires independent
semantic reconciliation, full affected-scope validation, pin rechecks under leases,
durable before/after evidence and restart recovery. No deletion, timestamp editing,
event replacement, settlement or delivery replay is supplied here.
