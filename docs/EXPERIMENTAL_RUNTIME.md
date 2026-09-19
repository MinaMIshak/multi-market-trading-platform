# Experimental runtime integration

`app.experimental.create_experimental_app` reuses the existing TODAY and M7
renderers. It takes an explicit absolute existing state directory and full Git
commit identifier. TODAY reads only `platform.db` in that directory, read-only;
missing data remains unavailable and no database is initialized. It does not
consume `EGX_DB_PATH`. Both HTML pages and JSON outputs identify the build and
EXPERIMENTAL / PAPER ONLY status. Process health does not imply data admission.
M7 performance remains unavailable until an authenticated report is integrated.

The factory has no scheduler/execution startup hooks, write endpoints, provider
calls or broker connections. Ordinary `app.main` routes retain their behavior.
This is a runtime integration component, not a new readiness dashboard.

## Outstanding launch gate

No runtime was launched in this milestone. A bounded socket inventory excluding
port 8000 showed no listeners on experimental candidate ports on 2026-09-19.
That observation neither reserves a port nor proves future availability. No
process command lines, environments, production paths or service configuration
were inspected. Repository topology remains FastAPI/server-rendered HTML,
SQLite, and a separate scheduler; existing Docker/Compose must not be used.

After the supervisor commits this component, implement a launcher that checks a
clean committed source tree, exports a separate immutable source snapshot, runs
the appropriate offline quality gate, binds only an available non-8000 loopback
port, supplies a separate owned state directory and records the exact Git commit.
It must use a minimal credential-free environment and no reload worker. A
candidate must pass HTTP health, build-identity and paper-label checks before
promotion. Preserve the previous process/snapshot on failed checks. Atomic
promotion and last-known-good retention are not implemented by this factory.

The commit parameter is syntactically validated, not authenticated by the app;
the launcher must bind it to the exported Git tree. The filesystem must remain
owned by the experimental operator; symlink checks are defensive and are not a
security boundary against concurrent malicious filesystem mutation or hardlinks.
Never copy production state. Do not initialize fixture market data for visibility.
Loopback access does not establish remote user reachability; do not change host
networking, firewall, reverse proxy or production services to expose it.

Next integrate authenticated shadow readers through existing evidence/package
contracts. No arbitrary serialized report may become trading truth. M7, shadow
and M8 semantics remain separate; visibility does not validate trading results.

## Committed source candidate gate

`tools/experimental_snapshot.py` now prepares a candidate from a clean Git HEAD
using read-only Git commands. It exports only tracked `app`, `tests` and
`requirements.txt`, excluding runtime data, logs, environment files and deployment
configuration. Archive traversal, links and non-regular entries are rejected.
The output root must already exist, be owned by the operator, and have private
permissions (0700). Each attempt creates a unique directory and never changes
another candidate or a last-known-good pointer.

The shared read/execute-only Python runs the three existing experimental/TODAY/M7
UI test modules with a minimal environment, bytecode and pytest cache disabled,
and a 180-second timeout. These tests use isolated artificial inputs and require
no external API. This is not an OS network sandbox or a full regression gate.
Test output remains in `quality.log`. Failure leaves no `candidate.json` receipt.
Passing candidates receive an atomic receipt binding commit, archive hash,
individual source hashes, interpreter and test command. Source changes during
testing and changes to checkout HEAD/cleanliness reject the candidate.

This command prepares source only: it opens no listener, starts no application,
and does not promote a build. The source directory is not filesystem-immutable;
a launcher must reverify receipt/source integrity before execution, use separate
state, perform HTTP identity/label checks and preserve the running known-good
process on failure. Automatic refresh and remote reachability remain unfinished.
Run preparation only after the supervisor commits the coherent milestone.

## Pre-launch source re-verification

`experimental_snapshot.verify(repository, candidate)` re-exports the receipt's
full commit with read-only Git, checks the archive digest, and compares both the
receipt manifest and actual candidate files against that export. Modified,
additional, missing or symlinked source files fail closed, including source
changes accompanied by updated receipt file hashes. Candidate directories must
be private and operator-owned; symlinked receipts are rejected. The recorded gate,
interpreter, mode and successful exit status must match the preparation contract.
An older committed candidate can still verify after checkout HEAD advances, so
ongoing development does not invalidate the last-known-good source.

On 2026-09-19, actual preparation of committed HEAD `8e02166` passed its isolated
21-test UI gate; the new verifier also accepted that candidate against Git.
This verifies source provenance, not a cryptographic attestation that tests ran:
receipt ownership remains trusted. Concurrent operator mutation, hardlinks and
post-verification edits are outside this integrity check's guarantees. A launcher
must call it immediately before starting the candidate and enforce its HTTP
promotion gate. No runtime or current-build pointer was created by this milestone.

## Loopback launch and promotion component

`python -m tools.experimental_runtime REPOSITORY CANDIDATE PRIVATE_RUNTIME_ROOT`
verifies the committed candidate, reserves a new loopback socket (default ephemeral
port; 8000 is rejected), and starts one Uvicorn child with that inherited socket.
Use the authorized shared interpreter and `PYTHONDONTWRITEBYTECODE=1`. No Docker,
service manager, scheduler, reload worker, provider environment or production data
is used. Each launch creates a separate empty state directory; missing inputs
remain unavailable rather than becoming fixture recommendations.

HTTP checks cover health, TODAY JSON, TODAY HTML and M7 HTML, including full build
identity and paper labels. Source is reverified before atomically replacing
`current.json`; each run retains its own `runtime.json` and log. Failure stops
only the newly created child and preserves the prior pointer and process. Previous
successful processes are deliberately retained; automatic retirement, a stable
proxy URL, remote reachability and automatic refresh are not implemented. The
record's loopback URL is the inspection endpoint, not proof of remote access or
continued process health. Runtime restart recovery is not yet implemented.

This component is tested with stubbed transports/processes under the repository's
unchanged offline test policy. An attempted real HTTP integration test was blocked
by that policy; no successful live launch is claimed. After supervisor commit,
exercise the committed launcher with the verified source candidate and inspect
its actual loopback HTTP gate. Do not launch the uncommitted implementation.
