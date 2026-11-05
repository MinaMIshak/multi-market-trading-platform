# Autonomous delivery contract

Status: transaction core, restart recovery, supervisor coordination, component
composition, production backend and host invocation components implemented as offline
code; host provisioning and actual supervisor invocation pending; SYSTEM receipt projection implemented with host publication pending. No release was deployed by this work.
`tools/delivery_runner.py` deliberately has no production CLI or default host
adapter. Importing it cannot start, push or deploy anything.

The supervisor, outside the Codex sandbox, will supply a trusted adapter and call
`deliver(request, state_directory, pinned_source, adapter)`. The adapter
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

1. Provision trusted host policy for the implemented `PinnedGitSource` adapter
   described below and wire it into the supervisor. Verify the real existing
   deploy-key configuration outside this sandbox. Serialize with the commit
   bridge and Codex cycle.
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

## SYSTEM delivery receipt projection

`/api/system` and SYSTEM now include an allowlisted `delivery` view. The host
can configure `EGX_DELIVERY_RECEIPT` to an absolute regular file containing a
published journal copy; default is UNAVAILABLE. Publication/wiring is pending.
Do not grant the API access to the private transaction directory. The copy must
be host-owned, atomically replaced and accessible only to the API account;
raw journals must never be served by a static-file endpoint. The reader rejects
symlinks, nonregular files, malformed/oversized JSON, invalid identities, unknown
phases, naive timestamps and any value other than boolean false for live money.

Only enumerated statuses, full SHA identities, normalized timestamps and derived
acceptance results are returned. Raw errors, schedule backups, paths, arbitrary
metadata and nested host payloads are excluded. A success claim needs matching
host tests (including both socket tests), candidate and production acceptance,
origin verification and schedule identity. Rollback needs matching rollback
acceptance; older receipts without that evidence remain unverified. Synchronous
rollback now persists the same acceptance evidence as restart recovery.

All results describe historical receipt observations. Scheduler health and live
runtime verification are never inferred. Codex cycle, Claude review and EGX/US
chain fields remain null until separate trusted observations are connected; no
checkpoint prose or arbitrary journal metadata fills these fields. Existing
SYSTEM runtime/chain sections retain their independent evidence semantics.

## Supervisor coordination API

`tools/delivery_supervisor.py` provides `DeliverySupervisor(state, source, host)`.
This is tested coordination code, not an installed supervisor or production
adapter. The trusted host must wire all entry points to the same private state
folder; requests cannot choose an adapter, command, source repository or folder.

- Call `startup()` before accepting work. An existing journal is recovered under
  the delivery lock, including terminal receipt repair. Invalid journals and
  failed recovery propagate; never delete them or continue the writer on error.
- Hold `writer_session()` around the entire Codex process tree and subsequent
  commit bridge. It takes the supervisor lock, recovers first, then holds the
  delivery lock as well. This also excludes callers using the transaction API
  directly. Commit failure must prevent creation of a release request.
- After the writer/bridge session ends, construct a fresh identity-only request
  from the exact bridge-committed HEAD and call `release(request)`. The source
  gates still reject a changed HEAD or dirty tree; no stale request is refreshed
  automatically. Recovery precedes delivery under the supervisor lock.
- All locks are nonblocking; contention fails closed. Lock order is supervisor
  then delivery. Do not call release/startup from within a writer session.
- A process timeout must terminate and reap the writer **and all descendants**
  before releasing its lease. The optional run_cycle API now supervises ordinary process groups as described below.
  The host service must use process-group/cgroup supervision to prevent orphan
  writers after supervisor death; advisory locks alone cannot enforce this.

Tests cover real cross-process exclusion and descriptor release on process death,
interrupted cutover recovery before a new writer, independent release/schedule/
acceptance/cleanup failures, malformed journals, dangling symlinks and exception
cleanup. Actual supervisor installation, process-tree timeout integration, pinned
origin/deploy-key policy provisioning, isolated reporting release, receipt publication and
host acceptance are still pending. No runtime health is inferred from these tests.

## Pinned source transport

`tools/delivery_source.py` supplies `PinnedGitSource`, the host transport adapter.
The supervisor must construct it with the intended exact origin URL, absolute
SSH configuration path, SHA-256 of its approved bytes, and exact deploy-key
identity path. These are trusted host policy, never release-request fields.
Use this adapter rather than the generic `GitSource` transaction test seam.
Actual host policy provisioning and supervisor installation remain pending.

Before push and again afterwards it verifies branch, clean tree, exact HEAD,
fetch/push URL equality to the pinned repository, and `ssh -G` metadata for the
existing github-multimarket alias (github.com, git user, one expected identity,
IdentitiesOnly, strict host checking and batch mode). No private key is read by
this inspection. Changed config bytes, symlinks, Include/Match directives and
proxy/local commands fail closed. SSH is invoked with the pinned config, no
agent or multiplexed connection reuse, and cleared forwarding. Git hooks and
non-SSH protocols are disabled; inherited Git/SSH configuration overrides are
excluded. Push retains the explicit non-force SHA refspec and exact origin check.

The host must protect policy/configuration, SSH trust files and key files from
concurrent modification for the whole transaction. This adapter neither provisions
keys nor accepts host keys; absent existing trust makes the push fail. Offline
fault-injection tests and a local isolated `ssh -G` parser check do not establish
that the real deploy key works or that origin is synchronized. The host adapter
for immutable preparation, runtime validation/cutover and scheduler adoption is
still required. No release or network operation occurred during this work.

## Exact-source reporting validation bundle

`tools/delivery_bundle.py` implements the source-preparation component, not a
complete runnable release adapter. The trusted supervisor supplies a verified
baseline full SHA and an exact mapping of reviewed changes to digests (deletions
map to null). Each digest is SHA-256 of Git mode ASCII, a NUL byte, and blob bytes.
This binds executable-mode changes as well as content, including tests. Approval
must follow independent reporting-only review; the module cannot establish code
semantics. Never generate approval automatically from the candidate diff.

Preparation reads all tracked blobs from the exact SHA using ls-tree/cat-file,
not checkout files or archive export attributes. Symlinks and submodules fail
closed. No challenger is silently removed: any unapproved change rejects the
whole candidate. Tests/configuration and application source stay together under
`<full SHA>/source`; source files are read-only data and scripts must be invoked
with their interpreter. All tracked files are included, so the trusted source
must itself be appropriate for host validation; never expose this bundle through
a static web root. Existing bundles are rejected, and partial failures clean up.
Call preparation under the existing delivery lock and protect the bundle root.

Verification rechecks both the trusted Git objects and external approval policy,
all file bytes, read-only permissions, identity and manifest. Rewriting a local
manifest cannot bless modified source. This is a read-only source bundle, **not**
a release green signal: interpreter is null and validation is NOT_RUN. A pinned
release-local interpreter, dependency lock/wheel provisioning, durable host
publication, full/socket tests, runtime/RSS measurement, spare-port acceptance,
cutover and schedule adoption remain to implement/integrate. No host operation
or actual reporting-only source approval was performed in this cycle.


## Writer process lifetime and commit bridge

`DeliverySupervisor.run_cycle` now holds both leases across a trusted writer
command, complete process-group cleanup, and the trusted commit bridge command.
Commands and deadlines come from host configuration, never a delivery request.
`tools/delivery_process.py` launches a new POSIX session, closes inherited lock
file descriptors, inherits log handles, enforces deadlines, sends TERM then KILL
and checks Linux process membership until no live group member remains. This
cleanup also runs after a successful leader exit, so a leftover writer child
cannot overlap the bridge. Failed writers never invoke the bridge; failed bridges
never return success. No release request is constructed by this helper.

Before launch a durable writer-active.json marker is written. Supervisor death
or unconfirmed cleanup leaves the marker blocking startup, writers and releases.
Only host reconciliation after confirming containment is empty may remove it.
A known-clean command failure clears the marker but propagates the error.

This is not a complete containment boundary: detached descendants can escape a
POSIX group. Host installation MUST use a dedicated kill-on-stop cgroup covering
the supervisor and every child, prohibit daemonizing commands, and prevent
uncoordinated invocation. SIGKILL of the supervisor cannot run Python cleanup.
The marker prevents subsequent cooperating cycles from treating that death as
success; it does not itself terminate an orphan. Host cgroup wiring/verification
and real supervisor adoption remain pending. Tests use disposable local process
groups (including TERM-resistant children), not production processes or sockets.

## Offline release interpreter provisioning

`tools/delivery_environment.py` now prepares a separate immutable
`<environment-root>/<full SHA>/venv` tied to the verified exact-source bundle.
It is an implementation component, not an installed host adapter. Trusted host
policy must supply a canonical base Python executable and its SHA-256, a complete
wheel filename/hash mapping, and the exact expected installed distribution names
and versions, including the bootstrap pip/setuptools where applicable. No actual
production dependency policy has been approved or generated by this cycle.
The reviewer must reconcile that policy with the source requirements, platform,
full test dependencies and reporting-only source approval. The current ranged
requirements.txt alone is not a reproducible dependency policy.

Preparation verifies/copies only reviewed wheels, creates a fresh venv with
copied executables, installs explicit hashed wheel paths with no index, dependency
resolution, source builds or cache, checks exact inventory and `pip check`, and
verifies the copied interpreter. Commands use process-group supervision, isolated
Python and a minimal environment with LIVE_MONEY=false. Nothing installs into the
development venv. Only the standard internal lib64-to-lib symlink is removed;
other symlinks/special files are rejected. Executable permissions are preserved
while writes are removed. File bytes and directories are fsynced before return.

Venv launchers contain absolute paths: creation takes place at the final path,
never a renamed staging path. The last manifest plus read-only verification is
the completion gate; existing/partial directories cannot be reused. Ordinary
failures clean up; uncertain process cleanup retains the partial directory for
host containment reconciliation. Always invoke under the delivery lease and
verified kill-on-stop host cgroup. The host must block further releases on
CleanupError; this module does not install containment or manage the journal.

The supervisor must persist the returned manifest SHA-256 in its private durable
journal before use, and call `verify` with that independently retained digest
before tests, launch and cutover. Verification rechecks approved source, policy,
base interpreter, every environment file/hash/executable bit and immutability.
A rewritten local manifest cannot bless changed files. Run the interpreter with
`-B` (or a protected bytecode cache outside the release) to preserve immutability;
write logs/test caches/runtime state outside it. Scheduler adoption must target
this exact interpreter, never the checkout venv.

A venv still depends on the base Python standard library, shared libraries and
host OS. The host must pin/protect those dependencies for the release lifetime
(e.g. an immutable host runtime image); hashing the executable does not attest
that entire runtime. Wheelhouse, policy and parent directories must also be
host-protected throughout preparation. The inventory is environment evidence,
**not** passing full/socket tests or runtime acceptance: `validation: NOT_RUN`
remains explicit. Real local integration uses only an artificial offline wheel
in a disposable environment; production pins, host wiring and acceptance remain
pending. No strategy promotion is authorized.

## Durable preparation boundary

`tools/delivery_preparation.py` supplies `PreparedRelease`, a trusted host
preparation component integrating the exact-source bundle and offline environment.
The host constructs it from disjoint canonical repository, artifact, wheelhouse
and private receipt directories plus independently reviewed policies. It must be
called inside the existing delivery transaction lease and protected containment;
it does not acquire a nested lock or supply production defaults.

Preparation first durably records PREPARING, rejects existing artifacts/receipts,
then creates and verifies the bundle/environment and fsyncs the bundle before
publishing PREPARED with both manifest digests and a policy digest. Any failure,
including uncertain child cleanup or receipt publication failure, leaves a
blocking preparation marker. Host reconciliation is required; retries cannot
adopt orphan artifacts or re-bless an interrupted environment. The enclosing
host adapter must preserve that obligation during transaction recovery.

`verify(sha)` reopens the private receipt after restart, requires the exact
identity/policy and boolean false live-money setting, then verifies all source
and environment content against the retained digest. Call it before tests,
launch and cutover. Receipt directories and their parents must be host-owned
and protected from concurrent modification; a receipt is not a signature or a
substitute for that trust boundary. PREPARED means only preparation succeeded:
validation remains NOT_RUN. This component is not yet wired into a complete
HostOperations adapter. Exact-release full/socket tests, runtime image pinning,
runtime/RSS measurement, candidate service, cutover/scheduler adapters and host
installation remain pending. No production acceptance is claimed.

## Exact-release host test evidence

`tools/delivery_validation.py` provides `ReleaseTests` for trusted host adapter
wiring under the existing delivery lease and verified cgroup containment. It
requires a prepared release, disjoint external test-output/private-receipt roots,
and independently pinned host runtime files. The host must choose pins covering
its immutable runtime image and keep that image and all roots protected; this
component checks pin bytes, not container mounts or OS immutability.

It verifies preparation and runtime pins before execution and again after every
command exit, runs the release-local interpreter in isolated/no-bytecode mode,
and runs the entire `tests` directory with inherited pytest arguments cleared.
Temporary files, HOME and JUnit output live outside the immutable source and
venv. Only a minimal environment is inherited, with LIVE_MONEY=false. Required
plugins must be in the reviewed environment inventory. Tests that require writes
inside immutable source fail closed and need explicit test-output remediation.

The durable TESTING marker precedes execution. Any command error, timeout,
interruption, changed artifact, invalid report or receipt failure retains that
blocking marker; automatic retry cannot bless it. Successful evidence requires
zero command exit, no reported failures/errors, and exactly one passing JUnit
case for each required socket ID (no skip or duplicate). Evidence includes
report hash, exact command, preparation receipt hash, runtime pin digest and
start/completion times. Host-owned receipt storage is required; test process
output alone is not trusted evidence. The host must protect receipts from the
candidate test user and enforce filesystem isolation independently.

TESTED establishes only full-suite/socket evidence at this exact source. The
result deliberately retains `runtime_rss: false` and candidate NOT_RUN, so it
cannot pass the delivery transaction's complete validation gate alone. Runtime
measurement, spare-port acceptance, production/scheduler adapters and installation
remain pending. Local component tests simulate host execution; they do not rerun
the sandbox-blocked socket cases or attest a deployed release.

## Bound runtime and RSS evidence

`ReleaseTests.verify` reopens exact-release test evidence, checks its command,
preparation/runtime pins, report digest, passing socket cases and completion state.
The test runner now uses the bundle's `source/` directory for imports and pytest
collection; the bundle container itself is not the application source root.

`tools/delivery_runtime.py` adds `ReleaseRuntime`, called under the same host
lease and containment requirements. Trusted host policy must specify **both** EGX
and US full-scale reporting workloads, source-relative Python scripts, arguments,
immutable copied snapshot file hashes, time budgets and maximum RSS budgets. The
host must independently establish that those scripts/inputs cover the required
nightly workload and are reporting-only. Names or successful execution alone do
not establish full-scale coverage. No production policy or input selection is
provided or automatically approved here. Use a protected input tree and isolated
writable output roots; the candidate cannot write receipts or policies.

The pinned GNU time executable measures each release-local interpreter command.
Only minimal environment variables are supplied, including LIVE_MONEY=false.
Commands execute outside source in separate market output directories. Preparation,
test evidence, runtime pins, timer and snapshot hashes are rechecked after each
command even on failure. Zero exit, finite elapsed time and positive RSS within
both budgets are mandatory. GNU time RSS is a process high-water measurement,
**not total concurrent process-tree memory**; host cgroups must independently
limit aggregate memory and contain all children. Immutable runtime/OS/filesystem
protection remains a host installation requirement.

MEASURING is persisted before launching anything and survives every failure or
interruption. Successful measurements are fsynced and a MEASURED receipt binds
the exact test receipt and workload policy. Restart verification rechecks the
measurements and all dependencies before exposing `runtime_rss: true`. Neither
this component nor supervisor recovery may erase or adopt an incomplete component
receipt; host reconciliation is required. A successful gate still says candidate
NOT_RUN and cannot establish spare-port or production acceptance.

Local tests inject command execution and measurement output; they are not market
runtime evidence. Production workload policies, actual exact-release host full/
socket and runtime tests, spare-port adapter, cutover/scheduler wiring and host
installation remain pending.

## Spare-port acceptance component

`tools/delivery_candidate.py` supplies `ReleaseCandidate` under the enclosing
host delivery lease. It revalidates PreparedRelease, ReleaseTests and
ReleaseRuntime through the runtime verifier, then binds the runtime receipt and
all candidate policy pins into a durable CHECKING receipt before creating state
or opening a socket. Incomplete receipts and output directories are never erased,
retried or adopted by this component. Recovery must retain these artifacts until
host containment/resource reconciliation is complete.

The trusted host must provision a hash-pinned factory with
`create_app(source, state, commit)`, approved snapshot files and HTTP field checks.
The factory must load the exact release application's real configuration and
copied state, expose observed `commit` and strict boolean `live_money` fields on
the health/API routes, and exercise actual compatibility and SIG-v1 reports.
It must not manufacture success wrappers. The host independently reviews these
policies; no production factory or approved route mapping is supplied here.
Every gate requires nonempty expected field paths/values. Policy pins cover the
factory, inputs, route checks, launcher and deadline. Candidate code cannot write
the protected policy or receipt directories under the required host containment.

The component copies only explicitly pinned files to its isolated state root,
reserves an OS-selected localhost port before spawn, passes the listening FD to
the release-local isolated Python, and rejects ports 8000/8001. HTTP requests use
no proxy or redirects and enforce status, response size, exact release identity,
LIVE_MONEY=false and type-sensitive policy assertions. Bounded body reads check
the shared acceptance deadline between reads, including slow-stream responses.
Host containment must still bound the entire supervisor lifetime (including HTTP
header parsing and unresponsive OS calls). Receipts include response
hashes and observation routes. No production pointer or schedule is changed.

The shared process-group owner now also supports temporary servers. Every normal,
exception and interruption exit terminates the owned group; uncertain cleanup
prevents ACCEPTED publication. Host kill-on-stop cgroup containment remains
mandatory for supervisor death or detached descendants. ACCEPTED is recorded only
after cleanup and dependency revalidation. Reopening verifies the historical
receipt and its dependency/policy binding; it does not claim a process is serving.
Cutover must start/manage the production instance separately and revalidate it.

Tests use simulated sockets/HTTP/lifecycle and real local process-group cleanup.
No sandbox socket test was retried. Host factory and policy provisioning, actual
exact-release full/socket/runtime/candidate acceptance, atomic cutover, scheduler
rollback and supervisor installation remain pending.

## Host composition and component recovery

`tools/delivery_host.py` now supplies `ComposedHost(candidate, production)` for
`deliver` and `DeliverySupervisor`. The candidate's existing dependency chain
provides preparation, full/socket tests and both-market runtime validation.
Each completed gate is reopened from its durable receipt; candidate acceptance
and its entire dependency chain are reverified immediately before cutover,
schedule adoption and production observation. No in-memory success flag substitutes
for durable evidence. Existing receipts or orphan artifacts reject preparation.

The host must supply a trusted production backend, never loaded from requests:
`snapshot`, `cutover`, `adopt_schedule`, `production`, `restore_release`,
`restore_schedule` and `rollback_check` retain the HostOperations contract.
`assert_contained(sha)` must raise unless protected host cgroup/filesystem/runtime
containment is installed and verified. `cleanup(sha)` must idempotently reconcile
only transaction-owned validation resources, without stopping the serving release.
`quiescent(sha)` must return strict boolean true only after observing those
resources empty; it must also work when preparation never started.

Cleanup always calls the backend before inspecting component receipts. A complete
contiguous prefix of verified components is safe to retain; missing receipts with
artifacts, downstream receipts without predecessors, incomplete receipts, altered
policies/evidence and uncertain containment all leave RECOVERY_REQUIRED in the
transaction journal. Restart recovery cannot erase or upgrade those receipts,
and both production and schedule restoration remain callable independently of
candidate evidence. Host reconciliation must preserve evidence and resolve the
specific resource/artifact obligation before reuse; this adapter supplies no
blind reset or automatic receipt deletion.

Composition tests simulate host operations and exercise transaction and restart
recovery, interruptions at all four component stages, orphan artifacts, evidence
tampering before cutover, strict containment truth, production/LIVE_MONEY failure
and release/schedule rollback. Actual host production backend, atomic routing,
cron snapshot/adoption/restoration, policy provisioning, containment installation
and supervisor invocation remain pending. No host acceptance or deployment is
claimed; the two socket tests must still pass on the exact approved host release.

## Durable production backend

`ProductionBackend` in `tools/delivery_production.py` implements the production
transaction behind `ComposedHost`. It has no default paths, installation action,
CLI, or request-supplied driver. The host must construct it under the existing
exclusive supervisor lease with protected, disjoint state and routing directories.
Its routing file is an atomic, fsynced JSON pointer containing full `commit` and
strict `live_money: false`. A host routing consumer must actually apply that
pointer; writing the file alone does not establish a successful cutover.

Before mutation it observes the previous production release, validates its exact
schedule and release-local interpreter through the driver, and persists the full
schedule plus its digest in `previous.json`. Cutover stages the persistent new
service while retaining the previous service, rejects route/schedule drift before
and after staging, atomically switches the pointer, then observes production.
Schedule adoption follows observed cutover, verifies the proposed release-local
schedule, installs it and reads it back. Restoration reopens and compares the
persisted backup, restores routing and schedule independently and is idempotent
across a fresh backend instance. The enclosing delivery journal retains the same
snapshot and latches failures for recovery. Backup state is protected by host
permissions and the lease; a hash alone is not an authenticity control.

The remaining trusted host services driver must implement:

- `contained(sha)`: observe cgroup and filesystem containment; return strict true
  only for verified isolation and immutable release/interpreter ownership.
- `stage(sha)`: idempotently start/retain the persistent SHA service and return
  actual HTTP/API identity, compatibility and applicable SIG-v1 acceptance. Never
  stop the previous verified service. This is separate from temporary candidate
  startup, whose process is already cleaned after acceptance.
- `observe(sha)`: probe the actual production routing endpoint, not a direct
  candidate port, with bounded responses and strict identity/LIVE_MONEY checks.
- `read_schedule`, `release_schedule`, `verify_schedule`, `install_schedule`:
  preserve exact prior schedule text, validate the full target and release-local
  interpreter/configuration, install atomically, and observe installed state.
  Missing/inaccessible schedule state must raise, not substitute an empty value.
- `cleanup_validation`, `validation_quiescent`: idempotently reconcile only the
  SHA-owned temporary validation resources, including when preparation never ran.
  They must never terminate the serving or retained rollback service.

Tests exercise real temporary filesystem pointers/receipts with simulated services
and schedules, including transaction restart recovery, LIVE_MONEY and acceptance
failures, schedule partial installation, snapshot corruption, concurrent state
drift, disk-write failure and idempotent rollback. They do not validate a real
routing consumer, service manager, cron installation or containment. Those host
bindings, supervisor installation, reporting-only approval, exact-release socket
and full-suite/runtime/spare-port acceptance remain pending. No release is green
or deployed on the strength of these simulations.

## Dedicated cron file binding

`tools/delivery_schedule.py:ReleaseSchedule` supplies the four schedule methods
for the trusted production services driver. It generates an exact dedicated
cron.d file from host-owned daily or weekday job times, entrypoints, user and immutable
bundle/environment roots. Every command changes to the SHA source and invokes
that SHA's interpreter with a clean environment and LIVE_MONEY=false. No request
can supply a shell command or an alternative schedule. The supplied release
verifier must independently verify the prepared release and immutable runtime;
file existence alone is insufficient. Job policy must include every required
chain and its release-local configuration before installation.

Read refuses missing, symlink, nonregular, oversized or group/world-writable
state. Install only accepts the exact approved generated text, writes a sibling
temporary file, fsyncs it, atomically replaces the dedicated file, fsyncs the
parent and checks exact readback. The existing production transaction retains
the entire prior file and can restore it after partial installation and restart.
Other cron files are untouched. Legacy schedules need explicit host migration
before the production snapshot can pass exact schedule verification.

The host must provision a root-owned cron.d target and protected parent, invoke
this binding with the proper owner under the delivery lease, retain the prior
release and policy for rollback, and independently verify cron daemon adoption
and chain heartbeats. File readback proves installed text, not daemon execution
or scheduler health. Host policy can now specify a sorted tuple of weekdays (Sunday=0 through
Saturday=6; empty means daily), module arguments, or a release-local `tools/*.sh`
entrypoint. Day-of-month/month remain wildcards, avoiding cron's day-field OR
ambiguity. Times use the host cron daemon timezone; configuring `TZ` in a job
would not establish its scheduling timezone. Host provisioning must verify it.
Arguments are shell-quoted; cron percent expansion, control characters, unsafe
script paths and duplicate jobs are rejected. Scripts must exist without symlink
traversal in the independently verified source bundle. Every command pins
`EGX_PYTHON` and `EGX_BUILD_REVISION` to the target release, including the existing
weekly learning script. Optional host-owned absolute configuration paths are
limited to `EGX_STATE`, `EGX_OPERATIONAL_DB`, `EGX_OPERATIONAL_DATA`, and
`EGX_US_DATA`; interpreter, environment and live-money overrides are rejected.
Exact verification includes configuration, weekdays and arguments. Host policy
must select all actual daily/weekly chains and retain that policy for rollback;
this component does not automatically infer or install it. No cron daemon was inspected
or changed in Codex. Filesystem tests include production-backend rollback after
an install succeeds and then raises. Routing consumer, persistent service and
containment bindings, host policy wiring and actual acceptance remain pending.

## Persistent service binding

`tools/delivery_service.py:ReleaseService` implements `contained` and `stage`
for host composition. The host provisions a separate full-SHA-named systemd
unit per retained release, an exact unit-file digest and loaded property policy,
a pinned systemctl executable, a release verifier, a direct-service API probe,
and an independent isolation verifier. No units are generated or installed by
this component. Invoke it only inside the supervisor's delivery lease.

Before starting, it checks the fragment digest, exact loaded execution properties,
absence of drop-ins/pending daemon reload, non-root service user, strict filesystem
protection, no privilege escalation, control-group kill behavior, disabled delegation,
and an exact SHA/disabled live-money environment. Start is idempotent and never
restarts or stops the prior release. Running state, PID and cgroup are required
before API acceptance and rechecked afterwards. The caller still requires SIG-v1
for new releases; older rollback releases may lack it.

The isolation callback must independently inspect actual host ownership, protected
runtime files/mounts, cgroup restrictions and running PID membership. Unit properties
alone do not establish containment. The callback must return boolean true, never
infer success from this module's policy comparison. Protected policies and roots
must remain immutable to candidate code and other writers throughout the lease.
The separate production observer must probe the routing endpoint; the stage probe
must not substitute for it. Routing consumer, temporary-resource cleanup, host
isolation verification, service provisioning, supervisor wiring and real acceptance
remain pending. Tests simulate systemd and API observations, with no host service
or socket access; their passing results do not establish deployment health.

## Reporting routing consumer

`tools/delivery_routing.py:ReportingRouter` is a WSGI consumer of the production
backend's atomic `{commit, live_money}` pointer. Protected host policy supplies a
unique loopback port per retained SHA and an exact public path allowlist. The
pointer and every ancestor must have the configured trusted owner and no group
or world write permission; symlink traversal, duplicate JSON fields, unknown
releases and enabled live money fail closed. Every request reads the current
pointer, so cutover and rollback need no cached-target reload.

Only GET/HEAD with no query string or request body are supported. This is a
reporting-only surface, not a general replacement for mutation APIs. The host
must explicitly select the required reporting and static paths and preserve its
existing authentication boundary. Client headers, cookies and credentials are
not forwarded; any upstream authentication requirement needs separate reviewed
host wiring. No user-supplied upstream or redirects/proxies are accepted. Response
bodies are buffered with a 4 MiB cap and deadline checks; only a validated content
type and locally generated length/cache/security headers are returned. Failures
return a generic unavailable response without exposing host details.

The required `verify_service(sha)` callback must independently observe the
specific configured port's exact release identity, disabled LIVE_MONEY, API and
runtime compatibility, and its persistent-service containment. It must not probe
this router (which would recurse), return cached acceptance or merely echo policy.
The callback runs before and after fetching; the pointer is reread before response
publication. A mid-request cutover discards the buffered old response. Requests
already completing when a pointer changes can finish on the retained old release;
this is not a connection-draining protocol. The production observer still probes
the public routing endpoint to establish actual cutover acceptance.

No listener, service, host policy or production route was installed by this work.
Tests use disposable filesystem pointers and simulated HTTP, including cutover,
rollback, tampering, unknown identities, safety violations, response limits and
request-time drift. Independent containment verification, composition/provisioning,
public endpoint acceptance and exact-release socket tests remain required.

## Independent persistent-service containment observations

`tools/delivery_containment.py:ServiceContainment` supplies the independent
`ReleaseService.verify_isolation` callback for protected host wiring. It performs
read-only Linux procfs/cgroup-v2 observations; it neither provisions isolation nor
starts services. Policies select exact release SHA, dedicated non-root UID/GID,
leaf cgroup, protected artifact roots, pinned executable, and exact writable mount
points. Protected roots and their ancestors must be host-owned and not group/other
writable or symlinks; the existing release verifier must independently validate
all source/runtime content. Policies must include the release, interpreter/stdlib,
OS/runtime dependencies, and delivery control artifacts relevant to that service.

Running acceptance checks every leaf-cgroup member for exact credentials, no
supplementary groups beyond the dedicated group, zero capability sets (including
bounding/ambient), no-new-privileges and seccomp-filter mode. It checks exact
cgroup membership and executable, a mount namespace different from the observer,
read-only protected paths, no nested mounts hiding protected content, matching
host/process-visible protected inode identities, and an exact writable mount set
with nosuid/nodev/noexec. Missing observations, nested cgroups, membership changes,
and PID start-time changes reject acceptance. An inactive service gets only
artifact preflight; `ReleaseService.stage` still requires running checks after
start and after HTTP acceptance. No inactive result establishes runtime isolation.

This deliberately restrictive policy may reject a normal systemd service layout;
the host must provision a compatible dedicated sandbox, never relax the verifier
to accept unreviewed mounts or child executables. Seccomp mode does not prove the
filter contents: host provisioning must pin the filter/unit and enforce kill-on-stop,
closed inherited descriptors, network/device restrictions, protected UID separation,
and immutable OS/runtime dependencies throughout the lease. These observations
are not an atomic kernel attestation or a replacement for that host boundary.
Temporary validation jobs and supervisor containment still need their own binding.
Tests use artificial procfs/cgroup files and simulated ownership policy; no real
host containment, socket validation, service installation or cutover is claimed.

## Protected production component composition

`tools/delivery_bindings.py:ProductionServices` connects `ReleaseService` (whose
isolation callback is `ServiceContainment`), `ReleaseSchedule`, a public HTTP/API
probe, and independent temporary-job cleanup/quiescence callbacks to
`ProductionBackend`. Construct these objects only from protected host policy.
The adapter has no production paths, installation defaults or request-driven
factory. Host provisioning must bind every component to the same approved source
and environment identities, and protect the policies and installed runner code.

Use `ProductionServices.router(...)` to construct the reporting WSGI application.
Its observer is `ReleaseService.observe`, which verifies running state,
containment and direct HTTP/API acceptance before and after probing without
starting a service. Inactive services return unavailable. Router requests never
call `stage`, change the schedule, or recurse into the public production probe.
The public probe supplied to `ProductionServices` must traverse the installed
router/listener/authentication boundary. Direct upstream success alone cannot
establish public production health. The adapter rechecks direct-service
acceptance after public probing; `ProductionBackend` additionally gates SIG-v1
for new cutovers and verifies the exact installed schedule. Previous verified
releases may lack SIG-v1 and remain eligible for rollback.

Integration tests exercise real temporary routing/schedule files, the service
policy parser, simulated manager/HTTP evidence, new-release schedule adoption,
restart rollback, public acceptance failures, isolation drift, and uncertain
temporary cleanup. These are composition tests, not host deployment evidence.
Protected provisioning, actual public probes, temporary-job/supervisor cgroup
bindings, listener installation, cron daemon adoption and exact-release
full/socket/runtime/spare-port validation remain pending. Cleanup callbacks must
only reconcile temporary validation resources; they must never stop persistent
serving or rollback services. A false or non-boolean quiescence result fails closed.

## Temporary-job cleanup binding

`tools/delivery_jobs.py:JobContainment` supplies `cleanup(key)` and
`quiescent(key)` for `ProductionServices` temporary-validation callbacks. Protected
host policy maps release identities to dedicated, pre-created cgroup-v2 leaves.
Groups must be disjoint, protected against job writes/migration, and must exclude
persistent serving/rollback services. The verifier rejects nested groups and any
group containing the calling supervisor. Missing controls fail closed.

Cleanup writes the kernel `cgroup.kill` control, then waits within a finite deadline
for both `populated=0` and empty `cgroup.procs`, checking group identity before and
after. It raises `CleanupError` for timeout, interruption, malformed observations,
unsafe controls or replacement. Callers retain their unresolved-work latch on
failure. An empty observation is valid only under the delivery lease with the
host launcher excluded from admitting new jobs.

`JobLauncher` now supplies `run(key, argv, ...)` and `process(key, argv, ...)`.
A protected, resolved host Python interpreter starts an isolated stdlib bootstrap,
writes zero (the calling process) to an already opened `cgroup.procs` descriptor,
closes that descriptor, and execs the target. Failed admission prevents target
execution. This avoids thread-unsafe `preexec_fn` and shell interpolation. Explicit
candidate descriptors can be passed through; the admission descriptor is closed
before target execution. Every exit path after attempted launch performs process
group cleanup followed by cgroup cleanup, with group identity checks. A busy,
missing or unsafe admission group raises `CleanupError` before launch.

`DeliverySupervisor(..., launcher=...)` now requires an explicit protected launcher
for `run_cycle`. It uses separate `writer` and `bridge` keys and retains its durable
writer latch on uncertain cleanup; bridge execution follows writer cleanup under
both locks. Missing launcher configuration rejects the cycle before execution.
Only protected host composition may inject this dependency; there is no launcher
selection in a release request.

The launcher is admission/cleanup plumbing, not containment installation. Host
wiring must deny migration/delegation, exclude other admissions during the lease,
verify filesystem/privilege isolation, pin the interpreter and its stdlib/OS, and
enforce supervisor-death cleanup. Release environment/test/runtime/candidate
components now require explicit `launcher=` bindings for execution. Host wiring
passes the protected JobLauncher to PreparedRelease, ReleaseTests, ReleaseRuntime
and ReleaseCandidate, with disjoint `environment`, `tests`, `runtime`, and
`candidate` leaf policies (also disjoint from `writer` and `bridge`). Environment
commands and both sequential market workloads finish cgroup cleanup before the
next command starts. Candidate startup forwards only its spare listener descriptor.
There is no fallback to process-group-only execution. Read-only receipt verification
does not require a launcher and does not establish current containment.

PREPARING, TESTING, MEASURING and CHECKING receipts survive uncertain admission
or cleanup; no completion receipt is published. Partial environments are retained
on CleanupError so a possibly live child cannot race deletion. These bindings
remain host-owned Python dependencies, never release-request parameters.
Host provisioning and kernel-level admission/cleanup acceptance remain pending.
Tests use artificial cgroup files, actual disposable subprocesses, and simulated
population cleanup. They prove bootstrap ordering and latch behavior, not kernel
cgroup placement, detached-child kill behavior, sockets or deployment acceptance.

## Unified host job composition

`delivery_composition.HostJobs` requires exactly six disjoint temporary leaves:
writer, bridge, environment, tests, runtime and candidate. Explicit persistent
service/rollback group exclusions are mandatory. `compose_supervisor` binds one
launcher to the supervisor and all four acceptance components, and binds aggregate
release cleanup to ProductionServices. Conflicting component launchers are
rejected. Policy or component-binding drift blocks new work.

The supervisor recovers unresolved delivery state before preflight. Preflight
checks protected state/interpreter paths, an explicit host isolation attestation,
and empty membership for all six leaves before writer or release admission.
Writer preflight holds both locks; release preflight holds the supervisor lock
before the runner acquires its delivery lock. Host installation must route every
writer/release through this supervisor and prohibit direct concurrent runner
invocation. The host isolation callback must verify provisioned migration,
filesystem, privilege, interpreter-dependency and supervisor-death controls;
a constant callback is suitable only for test fixtures.

Recovery cleanup attempts all four release groups even if an earlier group fails,
then raises CleanupError if any cleanup was uncertain. It never kills the writer,
bridge, serving service or rollback service. Receipts and writer-active latches
retain existing fail-closed recovery semantics. Successful cleanup does not bless
incomplete acceptance receipts. Missing or populated groups block admission;
preflight does not kill unexplained jobs or clear writer latches.

These are offline composition guarantees. Host provisioning, real cgroup and
isolation enforcement, exact-release full/socket tests, workload measurements,
spare-port acceptance and production adoption remain pending.

## Protected host invocation boundary

`delivery_entrypoint.HostEntrypoint` is an installed-supervisor API, not a
privileged CLI. Protected bootstrap code supplies a configuration path, its
SHA-256, the host owner (root by default), and a zero-argument composition factory.
Neither a release request nor the writer may supply those inputs. The factory
must construct the existing composed supervisor with its isolation preflight,
launcher and pinned source transport. Host installation must protect the bootstrap,
Python dependencies, factory and command scripts against writer/release mutation.
The entrypoint does not establish those installation facts itself.

The strict `delivery-host-v1` JSON contains `schema`, `state`, `repository`,
`writer`, `bridge`, and boolean `live_money: false`. Each command contains only
`argv` (an argument array beginning with a protected absolute executable) and
`timeout` (finite positive seconds, at most 86400). State and repository must be
disjoint canonical directories. State and configuration ancestors must have the
configured owner and no group/world write permission or symlinks. The configuration
reader uses a bounded regular-file read, no-follow open, file identity checks,
duplicate-key rejection and an independently supplied digest. No shell or dynamic
import path is selected by a request. Command script/dependency immutability is a
host installation obligation; configuration hashing alone does not prove it.

`invoke('startup')` reconciles recovery only; it does not declare readiness.
`invoke('cycle')` recovers before running the configured writer then bridge under
the existing leases and isolation preflight. `invoke('release', request)` recovers
before handing the unchanged identity-only request to the gated runner. Every
invocation rechecks the configuration digest, process identity and composition
bindings. A restart does not clear writer latches or manufacture acceptance;
unresolved recovery prevents admission. Existing supervisor/delivery locks reject
concurrent work. This API adds no remote endpoint, setuid program, sudo path or
sandbox privilege. Restricting service invocation and excluding uncoordinated
writers remains a host enforcement requirement.

Tests use protected-read fixtures with ancestor checks mocked for temporary
paths and simulated supervisor methods. They establish parser, pinning, dispatch
and failure-ordering behavior only. Actual installed bootstrap/factory policy,
isolation attestation, delivery-evidence publication and exact-release host
acceptance remain pending.

## Installed bootstrap manifest gate

`delivery_bootstrap.InstalledBootstrap` wraps `HostEntrypoint` for installed code.
It is not an installer, CLI or dynamic loader. The host passes a manifest path, its
independently held SHA-256, the factory function object and the owner. The strict
`delivery-bootstrap-v1` manifest contains `schema`, `configuration`, `files` (at most
1024 canonical absolute paths mapped to lowercase SHA-256) and boolean
`live_money: false`. It names no factory, so neither it nor a request selects code.

Before the factory runs, the gate requires:

- the manifest digest and every pinned file, read through `protected_bytes`;
- the host configuration among the pinned files;
- a module-level factory whose loaded module `__file__` matches its code path, with
  that source pinned;
- a pin for the source of every loaded `tools.*` module, so a manifest cannot omit
  delivery code that the process has already imported.

The same checks repeat after construction and at every invocation, before dispatch.
Factory module binding and entrypoint identity drift also block dispatch.
A module imported after the last check is only detected at the next invocation.

These checks compare files on disk. They do not attest code already in memory, so
the host must verify the manifest before trusting the process and must protect
bytecode caches, for example by running with `-I -B`. Because `-I` removes the script
directory from the import path, the protected launcher must set that path itself.
Malformed or excessively nested manifest and host configuration JSON raises
`DeliveryError` before the factory runs. Files above the 1 MiB read limit
(such as interpreter binaries) cannot be pinned here and fail closed if listed. The
interpreter, stdlib, third-party packages and OS stay host obligations, and anything
outside `tools.*` is only covered if the manifest lists it.

Tests use temporary files with ancestor checks mocked. Manifest tests stub the
entrypoint; one integration test uses the real entrypoint with a simulated supervisor
to verify recovery-before-cycle ordering and recovery-failure rejection. They
do not establish an installed bootstrap, real file ownership or host acceptance. The
real manifest, delivery-evidence publication, isolation provisioning and exact-release
acceptance remain pending.

## Protected public delivery receipt

`delivery_publication.DeliveryPublication(state, destination, owner=0)` is a
host-side publication component. Trusted installed wiring must call `publish()`
after supervisor operations, including failed operations, **outside** their lock
contexts. It acquires supervisor then delivery leases and reads the journal with
the protected, bounded reader. Concurrent operations reject publication. State and
public directories must be disjoint, canonical, host-owned and protected through
all ancestors; the destination is fixed by installed wiring, never a request.

Only a reconstructed allowlisted receipt is written. It preserves the existing
SYSTEM projection, including incomplete gates and rollback validation, without
copying raw errors, paths, schedule backups or arbitrary nested metadata. The
atomic write fsyncs the file and directory; public files are 0644, while private
journal writes retain 0600. Mount only the public directory read-only into the API
and set `EGX_DELIVERY_RECEIPT` to its published file. Do not mount private state.
The reader rejects duplicate JSON keys as ambiguous evidence.

Missing, malformed, oversized or unsafe source journals replace the published
view with UNKNOWN before reporting failure. Unsafe destination, lock contention
or filesystem write failure cannot guarantee a replacement; callers must record
publication failure and must not describe an older receipt as current health.
Publication is historical evidence only: cycle/review/market-chain observations
and current scheduler health remain UNKNOWN until separate trusted evidence
contracts exist. This component neither approves a release nor triggers cutover.

Tests cover sanitized projection parity, API reads, all transaction phases,
incomplete gates, invalid sources, unsafe permissions, symlink source, concurrent
locks and failed atomic replacement. Temporary-directory ancestor protection is
mocked; these are not installed-host ownership, mount or runtime tests. Installed
bootstrap must pin this module and `app/delivery_status.py` plus dependencies.
Host invocation wiring and real read-only API mount acceptance remain pending.

## Publication at the host invocation boundary

`HostEntrypoint` now supports `delivery-host-v2`: the existing strict host policy
plus a mandatory `publication` canonical absolute file path. The destination's
parent must be disjoint from both repository and private state; ownership and
ancestor protection use the existing publication checks. The installed bootstrap
must pin the updated configuration, publication module, `app/delivery_status.py`
and its dependencies. This is repository wiring, not an installed-host claim.
Legacy `delivery-host-v1` remains compatible but has no automatic publication;
unattended delivery installations must adopt v2 and the read-only API mount.

After startup, cycle or release, including recovery or operation failure and
Python interruption, the entrypoint publishes after operation leases unwind.
Successful publication preserves the original return value or exception. A
publication failure raises `PublicationFailure`, retaining `operation_error`
(or None) and `publication_error` for trusted supervisor diagnostics. Its public
message contains no raw exception detail. The supervisor must record this as a
failed invocation; it must not retry cutover just to retry publication. A completed
release remains governed by its durable journal and startup recovery. Abrupt
process death cannot execute this finalizer; subsequent startup republishes.

Configuration, invocation and binding rejection happen before publication or
recovery. Publication identity/destination mutation is rejected. Missing journals
clear public success to UNKNOWN and fail the invocation, including a first startup
with no delivery evidence. Contention and write failures propagate; an older
receipt may remain and is historical evidence, not current health. No observation
of cycle/review/chain freshness is inferred from publication time.

Regression tests exercise all operations with success, startup failure and
operation failure, reacquisition of real temporary-file leases, dual failures,
interruption, strict v2 policy, disjoint roots and binding drift. Services are
simulated and ancestor checks mocked. Real host installation, publication mount,
exact-release socket tests and spare-port acceptance remain pending.

## Cycle, review and chain observation contract

The optional fixed `observations.json` sidecar in protected private host state
uses schema `delivery-observations-v1`. Publication reads it under both leases
using the same protected bounded reader as the journal. Journal-supplied
observations are discarded. Missing, unsafe or malformed sidecars clear optional
observations on the next successful publication without invalidating independent
release receipts. No repository checkpoint or advisory review is automatically
converted into host evidence.

Supported record keys are `latest_codex_cycle`, `latest_claude_review`,
`egx_chain_status` and `us_chain_status`. Each requires `source_head` (full SHA),
`evidence_sha256` (64 lowercase hex characters), timezone-aware `observed_at`,
`live_money: false` and a bounded status. Cycle statuses are SUCCEEDED, FAILED or
INTERRUPTED; review is COMPLETED; chains are COMPLETED or FAILED. All other fields
are discarded. The trusted host producer must hash the actual evidence and bind
its source identity and event time before atomically writing the sidecar under
the supervisor/delivery leases. The digest is an attestation reference, not proof
that the API has independently read or validated the evidence. Review completion
is not endorsement, and chain completion establishes neither coverage nor fills.

API reads compute freshness from event time, never publication time: cycle 24h,
review 7d, each chain 4d (a coarse observation-age budget, not an exchange-calendar
or scheduled-session check). Exact budget boundaries are RECENT; older records
are STALE; any future timestamp is FUTURE. Status is historical and remains
visible beside freshness, source SHA and digest. None establishes live health,
satisfies a release gate, or authorizes strategy promotion. Invalid records remain
UNKNOWN independently. The existing SYSTEM table displays these structured
observations through escaped text. Scheduler health still remains UNKNOWN.

Host producer integration, immutable evidence storage, installed bootstrap pins
and actual publication mount acceptance remain pending. Tests use temporary
files and artificial attestations only.

### Protected event producer and first-publication behavior

`tools.delivery_observations.ObservationProducer(publication).record(...)` is the
host integration boundary for trusted cycle/review/chain completion callbacks.
Invoke outside both leases, supplying an event ID, full source SHA, bounded
status, actual event timestamp, strict `live_money=False` and a protected evidence
file. The caller must independently establish those semantics; arbitrary logs,
Codex checkpoint statements and Claude opinions are not completion attestations.
No CLI or repository-controlled callback is granted host authority here.

The producer takes both leases and reads bounded, protected regular-file evidence.
It hashes the exact bytes and durably stores a write-once, mode-0400 event receipt
in private state before atomically advancing the sidecar. Receipts contain the
exact evidence bytes encoded as hex; they must never be mounted into the API.
Event IDs are scoped by observation kind. Reusing an identity with changed content,
future timestamps, older events, equal-time conflicting observations, malformed
existing sidecars and unsafe evidence fail closed. Retry of an identical event is
idempotent. A failure after archive persistence can leave an unreferenced receipt;
it does not imply successful publication. Host-owned storage must remain protected;
these permissions are not an adversarial root immutability guarantee.

Publication runs after producer leases unwind. When no delivery journal exists,
valid sidecar records use `delivery-observation-publication-v1`; the API shows the
observations while delivery remains UNAVAILABLE with no tests, deployed identity
or scheduler claims. Missing journal plus missing observations still clears old
publication and fails. An unsafe or malformed journal also clears publication and
fails; it is not treated as an initial installation.

Installed completion callbacks, retention policy for private evidence, bootstrap
pins and read-only publication mount acceptance remain host integration work.
Temporary-file regression tests establish producer behavior only.

### Pinned host completion callback

`InstalledBootstrap.completion(path, digest)` now connects trusted host completion
notifications to the observation producer through `HostEntrypoint.completion`.
It loads callback dependencies before checking installed-module pins, rechecks the
entrypoint identity and requires delivery-host-v2. The envelope must be a protected
file beneath private host state; repository files cannot be supplied directly.
The callback does not invoke recovery, writers, bridges or releases, so interrupted
operations can be reported without clearing their recovery latches.

The host constructs `delivery-completion-v1` JSON with exactly `schema`, `kind`,
`event_id`, `source_head`, `status`, `observed_at`, `live_money` and `evidence`.
`evidence` is a nonempty string containing the retained completion evidence.
The other fields follow the observation contract above. The host supplies SHA256
of the entire envelope as `digest`. Parsing checks this pin, and the producer
rechecks it after reacquiring both leases before archiving the exact envelope.
Duplicate/extra fields, malformed JSON, invalid observations, changed bytes and
concurrent operations fail closed. Identical retry repairs interrupted publication
without authorizing delivery. Public output contains only the sanitized observation
and envelope digest, never its private evidence text.

Trusted installed supervisor/reviewer/chain callers must independently establish
completion semantics and preserve the envelope for retries. A protected file and
hash alone do not prove those semantics. This repository implements the callback
boundary; installed callers, protected storage retention, manifest provisioning,
API mount and actual host acceptance remain pending. No sandbox privileges change.
