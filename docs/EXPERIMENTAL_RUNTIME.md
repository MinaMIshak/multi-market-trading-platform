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
