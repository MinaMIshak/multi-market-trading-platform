# Autonomous delivery contract

Status: transaction core implemented; host adapter, supervisor invocation, recovery
runner and SYSTEM receipt projection pending. No release was deployed by this work.
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
  Recovery must be idempotent and preserve the previous verified release. This
  cycle does not yet implement automatic restart recovery.

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
6. Implement idempotent crash recovery under the delivery lock, supervisor timeout
   handling and automatic reconciliation of nonterminal journals.
7. Connect allowlisted delivery receipts to SYSTEM: source HEAD, origin sync,
   deployed release, Codex cycle, Claude review, tests, candidate validation,
   deployment/rollback, schedule target/health, EGX/US chain status and blocker.
   Unobserved fields must remain UNKNOWN; receipts must not imply current health.

No host path, socket, production process, cron or remote was accessed by the unit
tests. Fault injection validates orchestration, not the missing adapter or a real
release. LIVE_MONEY=DISABLED; automatic model/strategy/risk promotion is unauthorized.
