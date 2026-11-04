# Autonomous delivery contract

Status: transaction core implemented; restart recovery implemented; host adapter, supervisor invocation
and SYSTEM receipt projection pending. No release was deployed by this work.
`tools/delivery_runner.py` deliberately has no production CLI or default host
adapter. Importing it cannot start, push or deploy anything.

The supervisor, outside the Codex sandbox, will supply a trusted adapter and call
`deliver(request, state_directory, GitSource(repository), adapter)`. The adapter
must not come from request text. The state directory must be supervisor-owned,
private and outside the source checkout. Codex remains the sole repository writer;
Claude reviews read-only. The supervisor owns commit/push/release operations.

## Implemented transaction

- Identity-only requests require a full SHA, reporting-only scope, explicit
  `live_money: false`, unique request ID and timezone-aware timestamp no older
  than 15 minutes. Future requests fail. No command/path overrides are accepted.
- A nonblocking advisory lock spans the entire transaction. All cooperating
  release and recovery tools must use this same lock and state directory.
- Source gates require the expected branch, clean tracked/untracked tree, exact
  HEAD and one matching fetch/push URL using `github-multimarket`. Push uses an
  explicit SHA refspec without force, followed by exact remote branch verification.
  The source is rechecked before cutover. The supervisor must prevent concurrent
  source writes throughout delivery; the delivery lock alone does not lock Codex.
- Host validation must attest the exact SHA, full suite, both previously blocked
  socket test IDs, reporting-only policy, and full-scale runtime/RSS acceptance.
  Missing/false gates cannot be overridden by the request.
- Candidate acceptance requires exact identity, HTTP/API, compatible state,
  SIG-v1 and strict boolean `live_money: false`.
- A verified prior release and exact schedule backup are durably journaled before
  any cutover attempt. Schedule adoption follows cutover. Production acceptance
  repeats candidate gates and verifies the scheduler target.
- Any partial cutover or schedule/post-cutover failure attempts both release and
  schedule restoration independently, then verifies rollback. Failed restoration
  or cleanup becomes `RECOVERY_REQUIRED`; no later delivery may proceed.
- Interrupted transactions remain nonterminal and block subsequent delivery.
  `recover(state_directory, adapter)` reconciles these under the same lock. It
  never resumes forward delivery: before cutover it cleans up the candidate; if
  cutover may have occurred it restores both the verified release and exact
  schedule, verifies rollback, and cleans up. Failed recovery remains blocking.
  Restore/cleanup methods must be idempotent across repeated process crashes.

Receipts use atomic replacement and fsync for file/directory durability. They
record historical observations, not ongoing process health. The supervisor must
restrict receipt directory writes and redact adapter failures before publishing
an allowlisted SYSTEM view. Do not serve rollback backups or raw exception text.

## Host adapter acceptance requirements still to implement

1. Verify the existing SSH alias resolves to the intended deploy-key configuration
   without exposing key material. Pin the intended repository, not merely the
   alias-shaped URL. Serialize with the commit bridge and Codex cycle.
2. Reuse the immutable SHA archive approach in `tools/preview_release.py`, with
   exact-source tests available in a separate validation bundle and a release-local
   pinned interpreter. Never install into the development virtualenv. Verify hashes
   before launch and before cutover; reject modified/pre-existing unsafe releases.
3. Enforce reporting-only change policy against the previous verified release.
   The current source contains opt-in DF-CFG-2 research code; that is not permission
   to include it in the first isolated SIG-v1 release or promote a strategy.
   Resolve the reporting-only release source explicitly; do not silently deploy
   current HEAD merely because unit tests pass.
4. Run required tests outside the socket sandbox at the exact candidate SHA.
   Claude's advisory claim of two passing tests is not a substitute for this gate.
   Start an owned spare-port process with copied compatible runtime state; inspect
   real HTTP/API identity, live-money setting and generated SIG-v1 reports. Measure
   full-scale wall time/RSS against explicit host policy limits.
5. Persist a verified prior release/routing target and exact installed schedule,
   atomically switch production only after acceptance, then install/verify a
   schedule targeting the immutable release interpreter. Restore both on failure.
6. Wire the implemented `recover` function into supervisor startup and timeout
   handling before accepting another delivery or starting a Codex writer cycle.
   Recovery is a host operation using the same trusted adapter, never request
   commands. A missing journal means no transaction to recover; malformed journals
   require host reconciliation and must not be deleted to bypass the gate.
7. Connect allowlisted delivery receipts to SYSTEM: source HEAD, origin sync,
   deployed release, Codex cycle, Claude review, tests, candidate validation,
   deployment/rollback, schedule target/health, EGX/US chain status and blocker.
   Unobserved fields must remain UNKNOWN; receipts must not imply current health.

No host path, socket, production process, cron or remote was accessed by the unit
tests. Fault injection validates orchestration, not the missing adapter or a real
release. LIVE_MONEY=DISABLED; automatic model/strategy/risk promotion is unauthorized.

## Restart recovery durability

The authoritative `delivery.json` journal is fsynced before the per-request
receipt. A crash between those writes therefore leaves the recovery obligation
visible. Terminal recovery repairs the receipt without host mutations. Request ID
`delivery` is reserved to prevent collision with the journal filename.

Recovery validates identity, phase, disabled live money and rollback metadata
before invoking host operations. A durable `RECOVERING` phase precedes restores.
Both release and schedule restoration are attempted independently, followed by
HTTP/API, identity, compatibility, live-money and scheduler acceptance for the
previous release (which need not have SIG-v1). Cleanup failure also blocks later
delivery. Successful recovery records historical rollback observations, not current
health. Recovery never pushes, launches a new release, or promotes a strategy.

Fault-injection coverage includes every host boundary, crashes during recovery,
independent restore/cleanup failures, invalid journals, unsafe rollback evidence,
lock contention, journal/receipt interruption and repeated terminal recovery.
The trusted adapter and supervisor integration remain necessary for real recovery.
