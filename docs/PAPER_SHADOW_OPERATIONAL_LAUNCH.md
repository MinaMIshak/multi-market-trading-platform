# PAPER/SHADOW operational launch

Phase 6 connects the existing components for manual EGX SWING v1 observation.
The mission baseline is `7c1eacf9052d635569f9790afac21b355cb47309` on
`agent/er1c-free-acquisition`. Three untracked Phase-6 implementation/test files
were already present at continuity inspection and were preserved and completed.
Prior phases are closed; this document does not reopen their evidence decisions.

The supported output is an **unsized WATCH**, PAPER/SHADOW, UNVALIDATED,
NOT SCORED, NO EXECUTION INFERENCE. A candidate is not a fill. Q01 risk, sizing,
liquidity and fill approvals remain incomplete; quantity is zero and risk is
NOT_EVALUATED. No BUY/SELL state conversion occurs. No future holdout blocks this
observation path, and no holdout outcomes are read. Q01–Q13 remain unchanged;
Q12's individual custodian is TBD, and Q14–Q17 remain unapproved.

## Software path

`tools.paper_shadow_launch` composes `app.paper.swing_launch` with existing
`DailyRefreshRuntime`/`DailyRefreshJob`, quota admission, immutable raw storage,
daily canonical pipeline and artifact repository. It invokes the job directly;
no scheduler dispatcher, mode change, calendar enablement or background job is
required. A single explicitly selected symbol must be one of COMI, EAST, FWRY,
ORAS or SWDY. These are runtime targets, not the Q05 cohort or recommendations.

Verification requires authenticated calendar facts, exact security-master
identity, current validated artifact-ledger/raw PIT integrity, the unchanged 260 valid-bar
refresh admission, dated reviewed universe membership for every consumed bar,
complete reviewed corporate-action coverage, daily semantic review and a reviewed
daily package bound to the actual raw receipt. Missing mandatory truth blocks.
Neither current aliases nor a provider name supplies historical eligibility or
usage rights. The existing trusted reviewer/package contracts remain the source
of authentication; local hashes do not independently attest source truth.

`PointInTimeDailyRepository` supplies `SwingEngine` with exactly
50/20/50/20 (minimum history / fast EMA / slow EMA / breakout). The named
`SWING-V1-Q03-v1` builder takes the canonical Decimal close, never M4 reference
floats, and validates its +/-0.5% entry band, -3% stop, +6% target and null targets
2/3 through `TradePlan`. Plan UUIDs bind rule, instrument, PIT audit and decision.
Next-session clocks, entry validity, NO_FILL and ten eligible-session holding /
tenth-close exit rules are preserved in the existing audited context. The shared
TradePlan has no holding-session field; this publication does not execute a
holding counter, scheduled exit, fill, P&L calculation or economic label.
Existing canonical exit precedence and Q03 label rules are unchanged.

Explicit WATCH admission flows through `ShadowCandidateAdmission`,
`StrategyShadowSelection`, `StrategyShadowRequest`, and
`produce_strategy_watchlist`. The existing isolated collection, completion,
candidate ledger and UI envelope are reused. The producer publishes `input.json`
last, audits the candidate, and refuses an existing directory. Partial artifacts
are never successful publication. Do not retry with a new directory to reconstruct
or backdate a missed collection.

## FIRST_REAL_SIGNAL_RUNBOOK

Run from `/home/egx-agent/work/egx-trading-platform-us`, using the existing installed
interpreter. The following paths are an explicit isolated local layout; they are
not claims that these inputs already exist. The operator must supply an initialized
local database and its matching admitted data root. Never copy a production DB.

```sh
export EGX_DB_PATH=/home/egx-agent/research-data/paper-shadow-operational/platform.db
export EGX_DATA_ROOT=/home/egx-agent/research-data/paper-shadow-operational/data
export EGX_SHADOW_DIRECTORY=/home/egx-agent/research-data/paper-shadow-operational/collections/first-swing-v1
export EODHD_API_TOKEN_FILE=/home/egx-agent/research-data/paper-shadow-operational/operator-token
export EGX_SWING_INPUT=/home/egx-agent/research-data/paper-shadow-operational/swing-launch.json
export EGX_PYTHON=/home/egx-agent/work/egx-trading-platform/.venv/bin/python
"$EGX_PYTHON" -m tools.paper_shadow_launch status
```

The DB must have the current canonical schema and security-master snapshot. The
data directory and collection parent must exist; the collection itself must not.
Paths must be absolute, unlinked and inside the CLI's allowed isolated roots.
The token must be in a readable regular file with no group/other permission bits,
1–4096 bytes. Provision it privately through the operator's normal mechanism;
never paste it into commands or source control. Status checks presence and file
permissions only, never token contents. It does not inspect other environments.
An initialized DB is not proof of fresh data. Token input is required for refresh
only; status, reference import, verify, publish and UI audit need no provider.

Obtain the exact launch JSON schema without constructing Python/Pydantic objects:

```sh
"$EGX_PYTHON" -m tools.paper_shadow_launch schema
```

Supply `swing-launch.json` with every schema field (including explicit nulls and
nested defaults): `schema_version=swing-paper-launch-v1`, chosen runtime symbol,
authentic stable `instrument_id`, explicit `decision_status=WATCH`,
`planning_rule=SWING-V1-Q03-v1`, `history_start`, `sessions`, `signal_session`,
`entry_session`, `clock_packages`, and `daily_package` (null until reviewed).
Use canonical JSON arrays, decimal strings and explicit UTC timestamps. No example
market sessions, identity, prices, evidence or reviews are fabricated by this tool.

`sessions` must cover every civil date from history_start through the entry date,
with separately authenticated market/instrument eligibility; no weekday inference.
Choose history_start from authentic history sufficient for the unchanged 260-bar
refresh gate, not just 50 bars. Signal and entry clocks must identify EGX/XCAI
sessions, with evidence packages covering their actual clock fields. Execute after
the signal close and strictly before next-session open minus 15 minutes. The CLI
refuses any intervening trading session and never backfills past cutoffs. Unknown
session truth is EVIDENCE_BLOCKED, not MISSED or a scored observation. This does
not modify Q13's forward interval, coverage or cutoff.

The local PIT repository also needs original JSON for `egx-universe-v1` on every
consumed market date, `egx-actions-v1` covering the history, and approved
`egx-source-review-v1` documents for those receipts and the refreshed daily raw
receipt. Existing canonical field contracts are in `app/data/reference.py`.
Register each supplied reference/review document using the same command, with its
actual nonsecret provider and source locator:

```sh
"$EGX_PYTHON" -m tools.paper_shadow_launch import-reference \
  --input "$EGX_REFERENCE_JSON" --provider "$EGX_REFERENCE_PROVIDER" \
  --source-uri "$EGX_REFERENCE_SOURCE_URI"
```

The three variables above are operator-supplied per document. The command returns
receipt ID/hash, preserves exact bytes, and does not author or approve a review.
Import reviews only when supplied by the authorized review process. No acquisition,
rights discovery or automatic attestation is performed.

Run one real refresh only after supplying a verified positive integer upper-bound
cost in `EGX_REFRESH_QUOTA_UNITS` and its nonsecret supporting reference in
`EGX_REFRESH_QUOTA_EVIDENCE`. No unit cost is assumed; all uses of that provider
account must share the canonical quota ledger. No automatic retry is performed.

```sh
"$EGX_PYTHON" -m tools.paper_shadow_launch refresh --input "$EGX_SWING_INPUT" \
  --authorize-provider-call --quota-units "$EGX_REFRESH_QUOTA_UNITS" \
  --quota-evidence "$EGX_REFRESH_QUOTA_EVIDENCE"
```

This is the **operator's** explicit provider invocation. Codex did not execute it.
Refresh prints ingestion/artifact IDs and bar count, with
`REFRESH_COMPLETED_SIGNAL_NOT_RUN`; this is not a successful signal. Obtain the
actual daily semantic review for that raw receipt, import it with the command
above, and supply the corresponding reviewed `daily_package` in the launch JSON.
The package must bind provider, SHA256, size, receipt time and source locator.
Previously admitted matching current data can skip refresh, but cannot skip review.

Then verify freshness/admission and generate/publish once:

```sh
"$EGX_PYTHON" -m tools.paper_shadow_launch verify --input "$EGX_SWING_INPUT" &&
"$EGX_PYTHON" -m tools.paper_shadow_launch publish --input "$EGX_SWING_INPUT"
```

`verify` independently executes calendar, identity, PIT, SWING and planning
validation, without publication or a provider. It can append canonical PIT audit
or rejection records to the isolated local DB. `publish` rechecks all inputs at
its actual decision time; an earlier verification cannot authorize stale output.
A valid no-pattern result returns READY_NO_SIGNAL without creating a collection.

For PUBLISHED_PAPER_SIGNAL, audit through the existing UI reader:

```sh
"$EGX_PYTHON" -m tools.paper_shadow_launch ui
```

The existing application reads the same `EGX_SHADOW_DIRECTORY` via `/shadow`,
TODAY and `/api/shadow`. On an already configured isolated local application:

```sh
curl --fail --silent --show-error http://127.0.0.1:8000/api/shadow
```

No service startup, deployment or restart is part of this runbook. CLI `ui` uses
the same canonical reader and works without a server. The API reports a frozen
record, not ongoing freshness: `NOT ESTABLISHED / FROZEN RECORD ONLY`. The launch
result reports FRESH only at the verified decision and identifies the last verified
session. System health or DB health never establishes freshness. Empirical status
remains NOT VALIDATED and live execution remains disabled.

| Status | Meaning / operator action |
| --- | --- |
| CONFIG_MISSING | Supply the named isolated paths, initialized DB, launch file, token for refresh or explicit invocation arguments. |
| DATA_STALE | Daily data does not reach the authenticated expected session; obtain fresh admitted data within the valid window. |
| DATA_INSUFFICIENT | Missing admitted history or required session coverage; obtain authentic history without reducing 260-bar admission or SWING's 50-bar minimum. |
| EVIDENCE_BLOCKED | Required identity/calendar/PIT/review/integrity/publication gate failed; correct the authentic input, never invent it or bypass a cutoff. |
| READY_NO_SIGNAL | All runtime prerequisites are fresh and SWING has no pattern; this is a successful runtime result, not a failure. |
| PUBLISHED_PAPER_SIGNAL | This invocation published an audited SWING WATCH from supplied admitted fresh data; not a fill or empirical validation. |
| SOFTWARE_ERROR | Unexpected implementation failure; retain local diagnostics/artifacts and report the defect. No successful signal is asserted. |
| NOT_RUN | Status/reference/refresh/verify/UI operation did not publish a signal; inspect the operation field. |

Success returns exit 0, blockers exit 2, unexpected software failure exit 1.
Malformed command-line syntax also exits 2 via argparse. Errors never print raw
provider exceptions or token values. Input provenance locators must be nonsecret.

Preserve incomplete authentic publication directories for audit; do not delete,
overwrite or backfill them. Only newly created pytest engineering artifacts may
be cleaned up under their isolated temporary test directory. Fixture output must
never be copied into a runtime Shadow collection. No real signal can be promised
within 1–2 days without the required authentic inputs, and no signal is guaranteed
even when they are present.

## Verification and handoff

Tests are labeled ENGINEERING_FIXTURE / NOT MARKET EVIDENCE / NOT A REAL SIGNAL,
use injected providers and pytest temporary DB/data/collection paths, and inherit
the repository's autouse socket prohibition. They exercise real refresh ingestion,
canonical/PIT/SWING, Decimal planning, admission, producer and existing UI/API,
plus missing/stale/insufficient/evidence, no-signal, duplicate and partial-write
paths. No fixture installs operational state or establishes market evidence.

Validation completed on Phase 6 with network-blocked repository tests:

- Phase-6 relevant regression: `230 passed in 170.30s (0:02:50)`.
- Complete repository suite: `2770 passed in 336.86s (0:05:36)`.
- `git diff --check`: clean.
- No autonomous provider call, scheduler enablement, production deployment,
  broker/live order, live-money action, or production DB mutation was performed.
- Engineering fixtures remained test-only and were not presented as authentic
  market signals.

Therefore:

`PAPER_SHADOW_SOFTWARE_STATUS: READY`

The authentic runtime has not been invoked by this phase. It still requires the
operator-supplied admitted identity/security-master state, authenticated
session/calendar evidence, reviewed universe/actions/daily evidence, isolated
DB/data/shadow paths, and authorized provider configuration where refresh is
required.

`FRESH_SIGNAL_RUNTIME_STATUS: READY_FOR_OPERATOR_INPUT`

Software readiness is separate from authentic runtime readiness, empirical
validation, and live-money readiness.

## Operational dashboard binding

Configure the FastAPI dashboard component (`app.main:app`) with
`EGX_PAPER_RUNTIME=/home/egx-agent/research-data/paper-shadow-operational`.
This explicitly selects the isolated Paper/Shadow `platform.db` for `/`,
`/api/today`, and `/api/paper-operational`. When configured, missing or invalid
operational evidence displays `EVIDENCE_BLOCKED`; it never falls back to legacy
canonical data as the active signal view. Without this setting, existing routes
retain their prior behavior. US trading logic is unchanged.

The existing `tools/paper_shadow_launch.py verify` path now records a hash-bound
`PAPER_SIGNAL_VERIFIED` audit receipt and returns `signal_status` (`WATCH` or
`READY_NO_SIGNAL`). Receipts retain the PIT audit reference, admitted source,
operational window, decision time, and expected entry session. The dashboard
reads these receipts in a read-only SQLite transaction; it does not evaluate
strategies or call providers. Freshness is explicitly **at verification**, not a
claim of continuous source monitoring. At the expected entry opening, the
receipt displays `DATA_STALE` and hides plan levels until a new verification.
A symbol with canonical history but no verification displays `NOT_READY`.

A WATCH receipt is an unsized candidate, not a published collection or fill.
Publication still uses the existing `publish` command and audited collection
contracts. Configure `EGX_SHADOW_DIRECTORY` separately for the specific published
collection when one exists. `READY_NO_SIGNAL` needs no collection publication.
The immutable full source remains intact; `history_start` scopes only the
operational evaluation. No receipt establishes empirical validation or enables
live money.

Deployment remains an operator action: deploy the validated revision and set
`EGX_PAPER_RUNTIME` on the dashboard component, then restart that component using
the established operator procedure. No deployment, port changes, scheduler
changes, or production restart are performed by this workflow.
