# Scanner v2 — first contract and card slice

Status: offline contract, read-only card and bounded EGX verification coverage
adapter and EGX-RANK-v1 reporting adapter validated offline; offline EGX card
report projection and code-declared SYSTEM feature status committed; retained
card-report read gate and protected read-only loader committed; read-only GET
route `/api/scanner-v2/egx/card-report` over the loader added (host validation
pending); trusted card-report publisher library and its read-only retained
evidence input loader added (host validation pending); a host job entrypoint
composing them (`python -m app.scanner_v2_card_report_job`) added (host
validation pending, not scheduled); a host job entrypoint retaining its
prerequisite identity/calendar observations
(`python -m app.scanner_v2_evidence_retention_job`) added (host validation
pending, not scheduled); both entrypoints are declared in SYSTEM feature status;
no scheduled runtime producer invokes them yet, so the route currently answers
NOT_CONFIGURED/UNAVAILABLE unless a host directory is published.
No strategy, model, risk policy, scheduler or Champion is activated or promoted.
LIVE_MONEY=DISABLED.

## Reused integration points

- `app.strategies.contracts.Candidate` retains setup identity, version, configuration,
  detection/cutoff, evidence and research-only semantics.
- `app.domain.models.TradePlan` validates the explicit decimal long plan geometry.
  Float candidate references are not converted into trading terms.
- `app.paper.shadow_candidate_admission` remains a separate explicit admission
  boundary. Creating a TradeDecision or card never calls admission or RiskEngine.
- `app.egx_scan` / `app.egx_universe_scan` already coordinate scoped coverage.
  The next adapter should reuse their accounting, while retaining US-specific
  identity/calendar/source admission through existing US research adapters.
- `app.ui.trade_card.render_trade_card` renders a read-only HTML fragment;
  `trade_card` provides its JSON-ready projection. No HTML card page is installed;
  retained card reports are served read-only as JSON (see the route section).

## Contract

`trade-decision-v1` binds the existing candidate to an explicit EGX/US source,
canonical instrument UUID, artifact hash, evidence references, completed-bar flag,
data availability and freshness window, and immutable decimal entry/stop/target
terms. Max holding sessions are required; flat-by is explicit and optional.
Only confirmed setup states can produce this reporting record. WATCH and invalid
setups are excluded. Risk status remains NOT_EVALUATED, confidence UNVALIDATED,
event risk UNKNOWN, and execution disabled. The deterministic digest covers all
serialized fields, including market, candidate evidence and source identity.

Input timestamps and price terms are preserved, not normalized retrospectively.
Identity means identical serialized contract content; alternate timestamp offsets
or decimal representations are not promised to yield the same digest.

Nested fields are immutable tuples/scalars/frozen models. The projection revalidates
unchecked model copies, labels expired/stale records without changing the original,
and rejects observation times before detection. HTML escapes all supplied text.

## Remaining gates

Source metadata is an adapter assertion, not proof of authenticity, licensing,
calendar correctness, exchange membership, or market-specific identity admission.
The market-specific adapters must bind these assertions to existing admitted
artifacts before runtime use. Candidate itself has no market field; the new
explicit source binding must be made by that trusted adapter, never guessed from
symbol text. Synthetic tests do not establish market coverage or provider health.

`app.scanner_v2.scan_egx` now delegates to `scan_egx_scope` with a bounded,
unique explicit selection (maximum 1,000), preserving its admission and coverage
semantics. It returns deterministic lexical ordering, requested/usable/scanned
counts, per-symbol exclusions and an unknown authoritative universe size.
Usable means verification passed at the verifier's decision timestamp, not current
provider health. Extra out-of-scope source/error keys and inconsistent coordinator
reports are rejected. Only fixed exclusion codes enter the projection.

The current legacy SWING verifier emits WATCH or READY_NO_SIGNAL only. Both are
excluded from the confirmed trade-decision contract; this adapter therefore emits
zero candidates and no cards. It does not accept caller-supplied trade decisions
as verified output. No WATCH-to-READY conversion is authorized. The coordinator's
existing audit-event write remains; no scan history or signal is published by this
wrapper. Synthetic mixed-status and 224-symbol fixtures are not live scan evidence.

Next bind an existing confirmed research setup to market-specific admitted inputs,
explicit decimal terms and the contract, with inspected artifact provenance.
Do not invent missing targets, triggers or risk decisions. Keep missing required
inputs visibly excluded. Then connect the read-only API/UI with coverage evidence.
No new breakout/ORB/RSI generators are part of this slice. The complete Scanner v2
milestone remains pending those adapters, runtime integration and evidence checks.

## EGX-RANK-v1 decision adapter (validated offline)

`app.scanner_v2_egx_rank.bind_egx_rank_report` projects one published
`egx-ranking-report-v1` artifact into trade-decision-v1 records. It is the first
confirmed-setup adapter: EGX-RANK-v1 already publishes explicit decimal entry
zone, entry reference, stop, T1 and T2 for STRONG_CANDIDATE/CANDIDATE.

- The artifact bytes must match an expected SHA-256 taken from a verified
  snapshot manifest. Report schema, rank version, admission, LIVE_MONEY=false,
  session, counts and record uniqueness are checked; drift fails the whole call.
- Published terms are copied exactly as decimal strings. Nothing is recomputed,
  rounded or invented. `target` is T1, which matches `TradePlan.target_1` and the
  simulator's half exit. T2 and the breakeven rule are carried in candidate
  evidence.
- `max_holding_sessions` = `MAX_HOLD + 1` = 11. `simulate()` exits at index
  `MAX_HOLD`, and index 0 is the fill session.
- The trigger is a new literal, `EGX_RANK_V1_NEXT_SESSION_ZONE_V1`. The
  champion's lifecycle rule (next session only, gap below zone = NO_FILL) is
  not the forward-shadow `LONG_ENTRY_ZONE_TOUCH_V1` rule, so it is not labeled
  as that rule.
- The trusted caller (`EgxRankBinding`) supplies the instrument UUIDs, session close,
  freshness window, next-session open and validity, from admitted security-master and
  calendar evidence. The report's `next_expected_session` is an unverified
  expectation and is kept only as evidence.
- WATCHLIST becomes SETUP_NOT_CONFIRMED and NO_TRADE becomes NO_TRADE. An
  unbound identity gives IDENTITY_UNBOUND, and invalid or missing terms give
  TERMS_INVALID. All of these are visible exclusions. A candidate record that
  contradicts the champion gates is schema drift and fails closed.
- Mapping champion CANDIDATE classes to Candidate state READY is a reporting
  label. It is not admission, risk approval or evidence of edge. Risk stays
  NOT_EVALUATED, confidence UNVALIDATED and promotion NOT_AUTHORIZED. The
  supervisor context records that the champion's trade construction is
  currently net-negative, and these cards must not suggest otherwise.

There is no runtime caller, binding producer, HTTP route or persistence yet.
Synthetic tests do not establish market coverage.

SYSTEM's existing Project checkpoint reports this slice and its limitations via
PROGRESS.json; it does not imply that the feature is deployed or scanning.

Validation of the preserved handoff found eight invalid-term fixtures accidentally
bound an out-of-scope identity; those fixtures now exercise term rejection with
valid scope. Source/candidate/contract failures propagate, while only decimal
parsing and term geometry failures become TERMS_INVALID. Stops must be below the
whole zone, T1 above it, T2 above T1, and collapsed zones are excluded.
Duplicate JSON keys and non-finite numbers reject the report, including unbound
records. Generation must precede the admitted next-session open; its timestamp
is retained in immutable evidence. This is a historical research projection,
not a statement that entry remains possible when a card is viewed later.

The projected constants have a literal configuration digest and drift guard;
this identifies the adapter's interpretation, not a producer-attested code digest.
Historical artifacts do not contain a producer configuration digest. Future
producer changes require explicit versioning and compatibility review.
No corporate-action clearance is inferred from split-adjusted prices: event risk
remains UNKNOWN. Missing daily artifact IDs omit that reference; ranking-byte
provenance remains, without claiming independently verified underlying bars.

Before API/UI integration, bind admitted identities/calendar and include verified
lifecycle evidence (sample size and measured net result), preserving negative or
inconclusive findings. No such performance artifact was verified in this cycle.
The handoff barrier remains for the supervisor to clear after commit; no route,
activation, new setup logic or strategy promotion is included.

## Historical identity projection

`app.scanner_v2_identity.project_egid_identities` reconstructs exact canonical
EGX identities from bounded preserved EGID bytes and a caller-trusted
`RawArtifactManifest`. It reuses `build_canonical_security_master`, verifies
hash/size/count/provider/asset/session and availability by observation time,
and rejects ambiguous, malformed or non-finite input. Requested symbols are
exact canonical identifiers; aliases are not guessed. Missing identities and
non-equities remain explicit exclusions. The immutable projection can supply
identity tuples and hash provenance to the existing rank binding and Trade Card.

This is **not admission**: ingestion receipt does not establish successful
security-master refresh admission, licensing, listing membership or validity
between snapshots. The mutable repository replaces snapshots and cannot supply
historical admission evidence. A same-session snapshot is required conservatively;
no stale-snapshot fallback or validity interval is invented. The projection has
`admission=NOT_ESTABLISHED`, does no IO and cannot activate the adapter.

Next persist/bind independently verifiable admission evidence and admitted calendar
windows before adding a runtime caller. In particular, the current `MarketSession`
status alone does not prove that a next-session schedule was known before entry.
Do not substitute the ranking report's next-session expectation for that evidence.
Lifecycle evidence and API/UI wiring also remain pending. The prior handoff was
committed by the supervisor; its barrier marker was absent at this cycle start.

## Durable security-master admission history

Schema v10 adds `security_master_admissions` to the existing database. The EGID
refresh job passes its verified raw manifest into `SecurityMasterRepository`;
the repository records the canonical snapshot, source hash/date/size/count,
receipt time, transaction recording time, prior instrument count and versioned
completeness policy in the same transaction as current-master replacement.
The existing equity/nonempty/90% retention checks are repeated under the SQLite
writer lock. Receipt failure rolls back replacement, and replacement failure
rolls back the receipt. Same-day conflicts and older refreshes after a newer
admission fail closed. Exact retries preserve the original receipt and times.

Receipts have a content digest and database update/delete guards. Exact historical
lookup requires provider, date and artifact hash; it never falls back to the
current master. Subsequent replacements preserve earlier canonical identity
snapshots. Legacy repository writes without a manifest create no admission claim.
This is an internal trusted-writer integrity record, not a signature or independent
proof of source authority, licensing, exchange membership or raw-byte authenticity.

The additive migration requires the existing explicit `allow_upgrade=True` gate;
it does not backfill old admission history. Host adoption must validate schema
compatibility and rollback against a copied state before release. No operator
or production database was migrated during implementation.

`recorded_at` is sampled inside the transaction, **not** the commit visibility
time. Do not use it alone as a historical availability cutoff. The next slice
must bind trusted post-commit observation evidence to these receipts and verify
raw-byte/canonical projection agreement before upgrading identity admission.
Calendar schedule evidence known before entry and lifecycle evidence remain
separate required gates. The existing identity projection still reports
NOT_ESTABLISHED; runtime/API activation and promotion remain unauthorized.

## Post-commit identity observation binding

`SecurityMasterRepository.observe_admission` reads an exact historical receipt
on a fresh connection and samples a timestamp after that read completes. It
returns a versioned envelope containing the canonical receipt JSON and SHA-256.
An uncommitted writer receipt is not visible; a backward clock rejects the
observation. Reading an older receipt today never creates earlier availability.
This method does not persist observations: a trusted caller must retain the exact
envelope before later historical use. Digests are integrity checks, not signatures.

`bind_observed_egid_identities` verifies that envelope, the raw manifest/bytes,
the admission policy/counts and the entire reconstructed canonical snapshot,
including unrequested instruments. Receipt, recording, post-commit observation
and decision timestamps must be ordered. The immutable result reports
INTERNAL_SNAPSHOT_VERIFIED only; its nested raw identity projection retains
NOT_ESTABLISHED because raw receipt alone still does not establish admission.
No current-master lookup or automatic fallback is used. Exact raw retries can
have later receipt times without rewriting the original admission evidence.

This is offline internal identity binding, not exchange-membership certification,
source-rights approval or trading admission. Trusted durable observation retention,
admitted calendar windows known before entry, lifecycle evidence and runtime/API
integration remain pending. No schema change, runtime writer or promotion is
introduced by this slice.

## Durable post-commit observation retention

Schema v11 adds immutable observation envelopes to the existing security-master
SQLite evidence store. `retain_admission_observation` obtains its own post-read
observation, then transactionally retains the first exact canonical envelope and
its digest. It accepts neither caller timestamps nor caller envelopes. Concurrent
producers and retries preserve the winner's original bytes and availability time.
A failed write creates no observation; loss of acknowledgement after commit can
be recovered by exact lookup or retry without rewriting time.

`get_admission_observation` requires provider, snapshot date and raw artifact hash.
Missing history raises rather than synthesizing an observation. Reads validate
envelope integrity, canonical encoding, exact committed receipt binding, disabled
live money and ordered recording/observation/current timestamps. Damaged evidence
is rejected and preserved. Database update/delete/replacement guards protect the
retained records; database-writer trust and clock correctness remain required.
This is internal evidence retention, not external attestation or source licensing.

The explicit `allow_upgrade=True` migration adds no historical observations.
Only temporary test databases were migrated. Host deployment must validate schema
compatibility and rollback on copied state. Callers may pass retrieved envelopes
to the existing offline identity binder; no runtime refresh, API, calendar or
lifecycle admission is enabled by this change.

## Retained calendar window projection

`project_egx_calendar` reuses `MarketSession` rows and the existing EGX weekend
policy. The offline caller supplies exact retained JSON bytes, expected SHA-256,
observation time and decision time. A bounded contiguous window must start with
a completed verified session, explicitly account for every intervening nontrading
day and end with an unverified future schedule whose open is after the decision.
Missing dates, unsupported weekend labels, contradictory hours, future verification,
late observation, duplicate JSON keys and nonfinite values fail closed.

The immutable result contains the historical close, next scheduled open/close and
byte provenance. It deliberately reports `admission=NOT_ESTABLISHED`. Caller
observation time and digest are trusted assertions, not durable admission evidence
or proof of exchange authority. UNKNOWN future rows describe schedule assertions;
VERIFIED completed-session status cannot stand in for advance schedule knowledge.
No weekly fallback, mutable calendar lookup, freshness interval, identity binding,
lifecycle admission or runtime/API activation is introduced. A trusted retained
schedule-admission producer and exact post-commit observation binding remain the
next gate before combining this projection with retained identity observations.

## Durable calendar observation retention

Schema v12 adds immutable content-addressed calendar observations to the existing
SQLite store. `TradingRepository.retain_calendar_observation` reads one committed
window from `market_sessions`, checks column/payload identity, samples its own
clock after the read and validates the exact bytes with `project_egx_calendar`.
It accepts dates only, with 2–32 contiguous rows. An exact retry preserves the
first observation; changed source bytes create a different digest. Lookup uses
only retained history and rejects missing, corrupt, future or unavailable evidence.
Neither method accepts a caller-supplied observation time.

The retained projection still reports `admission=NOT_ESTABLISHED`. These records
prove an internal observation under trusted database-writer and clock assumptions;
they do not establish source authority or advance schedule admission. Runtime/API
activation, identity binding and lifecycle admission remain gated.

The migration requires explicit `allow_upgrade=True`, adds no historical records
and preserves existing sessions. Tests exercise disposable databases only. A v12
database cannot be opened by v11 code: release-pointer rollback alone is therefore
insufficient after a shared-state upgrade. Before host adoption, validate both
candidate and previous-release state compatibility on copied state. Keep shared
state unchanged until a protected host migration/restore mechanism has demonstrated
rollback with preserved evidence and coordinated writers, or use separately pinned
compatible release state. Without that evidence, reject cutover. This unit does
not provide or execute a production migration or database restore mechanism.

## Retained-evidence rank binding composition

`app.scanner_v2_retained_binding.compose_egx_rank_binding` builds the existing
`EgxRankBinding` only from an `ObservedIdentityProjection` and a retained
`CalendarProjection`; callers no longer hand-assemble session, open, close,
identity tuples or evidence IDs. Both inputs are revalidated, and the immutable
`RetainedEgxRankBinding` cross-checks session equality, digest-derived evidence
IDs, identity ordering and calendar observation inside the completed pre-open
window. Unchecked copies are revalidated before use.

Freshness policy `FRESH_UNTIL_NEXT_SESSION_OPEN_V1` sets `fresh_until` to the
retained next-session open, so cards viewed at or after that open are DATA_STALE.
`evidence_available_at` is the latest of raw identity receipt, admission
observation and calendar observation (exact-retry manifests are conservative).
`bind_retained_egx_rank_report` reuses `bind_egx_rank_report` unchanged and
rejects ranking reports generated before that time: later evidence never
backdates a decision. Identity exclusions and admission statuses are reported.

No admission is upgraded: identity stays INTERNAL_SNAPSHOT_VERIFIED, calendar
stays NOT_ESTABLISHED, `runtime_activation=false`, LIVE_MONEY=false. No IO,
runtime caller, API route, schema change or promotion. Lifecycle evidence and
API/UI wiring remain pending. Tests use synthetic projections only.

## EGX Trade Card report projection

`app.scanner_v2_card_report.project_egx_card_report` is the read-only end of the
first vertical slice: retained evidence -> trade-decision-v1 -> Trade Card. It
accepts the exact ranking bytes, expected SHA-256, a `RetainedEgxRankBinding` and
the viewer's `observed_at`; it never accepts a caller-assembled decision dict.
It re-runs `bind_retained_egx_rank_report` and the existing `trade_card`
unchanged, and returns one JSON-ready `scanner-v2-egx-card-report-v1` document:

- `coverage`: requested ranking scope, classification counts, candidate and
  exclusion counts/reasons, identity exclusions and per-symbol rows. The scope is
  the published ranking scope; authoritative universe size stays `null`.
- `provenance`: ranking artifact hash, adapter/rank/config versions, session,
  generation time, latest retained-evidence time, freshness policy, identity
  (INTERNAL_SNAPSHOT_VERIFIED) and calendar (NOT_ESTABLISHED) admission labels.
- `cards`: one card per bound decision in lexical order; each decision ID must
  match its coverage row. `card_state_counts` reports RESEARCH_CANDIDATE,
  DATA_STALE (at/after retained next-session open) and PLAN_EXPIRED. Plan terms
  and decision IDs are identical for every viewing time.
- `lifecycle_evidence`: NOT_VERIFIED with null sample size and net result. No
  lifecycle/performance artifact is bound; nothing is inferred from candidates.
- fixed `limitations` and SHADOW / LIVE_MONEY=false / execution disabled /
  runtime_activation=false / promotion NOT_AUTHORIZED.

Viewing before ranking generation, naive timestamps, hash mismatch, forged
evidence copies and decision/row drift fail closed. Feature status is
`OFFLINE_PROJECTION_NOT_ACTIVATED`: there is no HTTP route, runtime producer of
retained bindings or persistence yet. Next: a read-only API route serving this
document from a host-retained artifact.

## SYSTEM feature status

`app.scanner_v2_card_report.feature_status()` returns code-declared constants
(schema, `OFFLINE_PROJECTION_NOT_ACTIVATED`, EGX adapter OFFLINE_PROJECTION_ONLY,
US adapter NOT_IMPLEMENTED, no API route or runtime producer, `published_cards`
null, fixed limitations, SHADOW, LIVE_MONEY=false, promotion NOT_AUTHORIZED).
`/api/system` exposes it as `scanner_v2`; the SYSTEM page renders it with escaped
values and UNKNOWN for missing/malformed fields. It is not a scan, card count,
deployment or runtime observation, and it does not read PROGRESS.json.

## Retained card-report read gate (pending host validation)

`app.scanner_v2_card_report_reader.read_retained_egx_card_report` is the read
side a future read-only loader/API route must use before showing a retained
`scanner-v2-egx-card-report-v1` document. It takes exact bytes (maximum 8 MiB)
and the SHA-256 recorded by the trusted producer, does no IO and has no caller.
Any failure raises `RetainedCardReportError` (a `ValueError`); callers must
report UNAVAILABLE and must not show partial content.

It checks:

- strict UTF-8 JSON (duplicate keys and NaN/Infinity rejected) and exact field
  sets at every level, so an added `expected_return` or BUY-style field fails;
- fixed identity/safety values: schema, EGX, OFFLINE_PROJECTION_NOT_ACTIVATED,
  SHADOW, LIVE_MONEY=false, execution disabled, runtime_activation=false,
  promotion NOT_AUTHORIZED, risk NOT_EVALUATED, the exact limitations, and
  lifecycle evidence NOT_VERIFIED with null sample size and net result;
- coverage arithmetic, recomputed from the rows: requested, candidate, excluded,
  classification and exclusion counts; unique lexical rows; exclusion reasons
  must match the adapter's class mapping; authoritative universe size null;
- provenance versions (adapter, rank, config digest, freshness policy), admission
  labels (identity INTERNAL_SNAPSHOT_VERIFIED, calendar NOT_ESTABLISHED) and the
  order evidence available <= ranking generation <= viewing time. Retained-binding
  session, availability and safety fields must agree with the provenance;
- each card in coverage order: decision ID, symbol, ranking artifact hash and
  retained calendar/identity evidence IDs, `fresh_until` equal to the retained
  binding, timestamp order, plan geometry (stop < zone < target, reference inside
  the zone), risk per share recomputed as entry reference minus stop, and fixed
  risk, research and confidence labels;
- each card's state, recomputed with the same rule `trade_card` uses: a stored
  RESEARCH_CANDIDATE or DATA_STALE label cannot hide an expired or stale plan.
  `card_state_counts` must equal the recomputed states.

- each card's exact `TradeDecision` is rebuilt from its retained fields through
  the existing `Candidate`, `DecisionSource`, `DecisionTerms` and `TradeDecision`
  validators (EGX-RANK-v1 sets no float candidate references, so they are rebuilt
  as null). The rebuilt `identity` must equal `decision_id`,
  `strategy_candidate_sha256` must equal `candidate_sha256`, and the unchanged
  `trade_card` projection of the rebuilt decision at the report's `observed_at`
  must equal the retained card exactly. A consistent edit (for example, stop and
  risk per share changed together, or a forged decision ID in both the row and
  card) is therefore rejected even when the report hash is recomputed.

Limitations: the gate does not re-run the binder or verify the ranking bytes or
underlying bars; coverage rows for excluded symbols and provenance fields outside
the cards are protected only by recomputed relations and the producer-recorded
hash. That hash is an integrity check, not a signature or proof of authority.
Decision digests are self-consistency checks, not evidence that the decision was
produced from admitted inputs. Nothing is admitted, filled or promoted. A trusted retained-binding
producer, retained report storage and the read-only route are still missing.
`feature_status()` is deliberately unchanged: there is still no route or producer.

## Protected retained card-report loader (pending host validation)

`app.scanner_v2_card_report_loader.load_retained_egx_card_report` is the IO
side of the read gate. It has no caller and no route yet. The host directory is
named by `EGX_SCANNER_V2_CARD_REPORT_DIR` (absolute path) and holds:

- `<sha256>.json`: exact report bytes named by their SHA-256 and never rewritten;
- `current.json`: `{"schema_version": "scanner-v2-card-report-pointer-v1",
  "report_sha256": "<64 lowercase hex>"}`, atomically replaced on publication.

Each load opens the directory and both files without following symlinks,
requires regular files, bounds reads (4 KiB pointer, reader's 8 MiB report),
parses the pointer strictly and hands the exact bytes and pointer hash to
`read_retained_egx_card_report`. A pointer replaced mid-load still names
immutable content-addressed bytes. The loader never writes.

It returns `scanner-v2-card-report-response-v1` with status `AVAILABLE`,
`NOT_CONFIGURED` or `UNAVAILABLE` and one fixed reason (`CONFIG_INVALID`,
`DIRECTORY_UNAVAILABLE`, `NOT_PUBLISHED`, `POINTER_INVALID`, `REPORT_UNREADABLE`,
`REPORT_REJECTED`, `REPORT_FROM_FUTURE`). `report` is set only when the reader
accepted it; `detail` carries only the reader's code-defined rejection text.
Deep-nesting `RecursionError` is rejected. A load clock earlier than the report's
`observed_at` fails closed.

Cards keep the state the producer computed at `observed_at`; they are not
relabelled. The response separately reports `report_observed_at`,
`load_freshness` (`WITHIN_RETAINED_WINDOW` or `STALE_AT_LOAD`, from the retained
binding `fresh_until`) and `cards_current_at_load` (cards whose freshness,
`valid_until` and `flat_by` still hold at load). A route must show these
alongside the cards, so an old report is never presented as current. Every
response carries SHADOW, LIVE_MONEY=false, execution disabled,
runtime_activation=false and promotion NOT_AUTHORIZED.

The pointer hash is integrity under a trusted host writer, not a signature.
Ancestor directories are a host protection obligation (only the final component
is opened without following symlinks). No producer publishes these files yet,
so `feature_status()` is still unchanged. Next: the trusted producer and a
read-only route serving only this loader's response.

## Read-only card-report route (pending host validation)

`GET /api/scanner-v2/egx/card-report` (`app.main.scanner_v2_egx_card_report`)
returns exactly `load_retained_egx_card_report()` with the real UTC clock. It
adds no fields, does no other IO, and has no write method. Without
`EGX_SCANNER_V2_CARD_REPORT_DIR` it answers `NOT_CONFIGURED`; any other failure
is the loader's `UNAVAILABLE` response with a fixed reason and no report.
HTTP status is 200 for every outcome; clients must read `status`/`reason`.

`feature_status()['api_route']` now names this path, so SYSTEM shows it instead
of `NOT_INSTALLED`. Status stays `OFFLINE_PROJECTION_NOT_ACTIVATED`,
`runtime_producer` and `published_cards` stay null and `runtime_activation`
false: installing a route is not a producer, scan, coverage or activation.
Next: a trusted host producer that writes content-addressed report bytes plus
an atomic `current.json` pointer.

## Trusted card-report publisher (pending host validation)

`app.scanner_v2_card_report_publisher.publish_egx_card_report(directory,
artifact, *, expected_sha256, evidence, now=None)` is the write side of the
loader. It has no scheduler or runtime caller yet. It:

1. runs the existing `project_egx_card_report` with its own clock as
   `observed_at` (no caller-supplied observation time);
2. encodes the document canonically (`encode_card_report`: sorted keys, compact,
   no NaN) and passes the exact bytes through `read_retained_egx_card_report`
   before any write, requiring a byte-identical round trip;
3. opens the host-provisioned absolute directory without following symlinks,
   refuses group/world-writable directories and takes an exclusive non-blocking
   `flock` (a concurrent publisher gets `PUBLICATION_BUSY`);
4. reads the current publication through the loader's own read path. Missing is
   a first publication; the same digest returns `ALREADY_CURRENT` with no write;
   a damaged pointer or rejected report is preserved and refused
   (`EXISTING_PUBLICATION_INVALID`); an existing report observed after `now` is
   `CLOCK_REGRESSION`; an older ranking `generated_at` is `RANKING_REGRESSION`;
5. writes `<sha256>.json` (mode 0444) by temporary file, fsync and hard link,
   never overwriting. Existing identical bytes complete an interrupted
   publication; different bytes or a symlink are `CONTENT_CONFLICT`;
6. atomically replaces `current.json`, fsyncs the directory and re-reads through
   the loader path, requiring the new digest (`PUBLICATION_UNVERIFIED` otherwise).

Projection and gate failures (`PROJECTION_REJECTED`, `REPORT_REJECTED`) and
refusals in step 4 write nothing. Old report bytes are retained, never deleted.
The publisher does not create the directory, provision ancestors, schedule
itself or verify the retained-binding inputs beyond the existing projection; a
trusted host job must supply manifest-verified ranking bytes and retained
evidence. `feature_status()` is unchanged (runtime producer null): a library
function is not a scheduled producer, scan, coverage or activation.
LIVE_MONEY=false; nothing is admitted, filled or promoted.

## Retained evidence input loader (pending host validation)

`app.scanner_v2_retained_evidence.load_retained_egx_rank_binding(artifact, *,
expected_sha256, identity_artifact, identity_manifest, security_master, trading,
calendar_sha256)` builds the publisher's `evidence` argument from already
retained storage, so the host job does not hand-assemble projections:

1. `ranking_scope` checks the ranking bytes (8 MiB bound, manifest SHA-256,
   strict JSON) and reads only `based_on_session`, an aware `generated_at` and
   the unique published tickers (sorted). The full ranking contract is still
   enforced later by the unchanged `bind_egx_rank_report`;
2. `SecurityMasterRepository.get_admission_observation` reads the retained EGID
   observation for that session and manifest digest, and the existing
   `bind_observed_egid_identities` binds it with `observed_at = generated_at`
   and `requested` = the ranking scope;
3. `TradingRepository.get_calendar_observation` projects the retained calendar
   window with `decision_time = generated_at`;
4. the calendar session must equal the ranking session, then the existing
   `compose_egx_rank_binding` builds the `RetainedEgxRankBinding`.

The ranking generation time is the only decision time: identity or calendar
evidence observed after it is rejected, so later observations never backdate a
decision. Only read methods are called; it never calls `retain_*` or
`observe_admission` as a substitute for missing retained evidence. Failures
raise `RetainedEvidenceError` (a `ValueError`) with one fixed reason:
`RANKING_INVALID`, `IDENTITY_OBSERVATION_UNAVAILABLE`, `IDENTITY_REJECTED`,
`CALENDAR_OBSERVATION_UNAVAILABLE`, `CALENDAR_REJECTED` or `BINDING_REJECTED`.

Admission labels are unchanged (identity INTERNAL_SNAPSHOT_VERIFIED, calendar
NOT_ESTABLISHED); runtime_activation=false; LIVE_MONEY=false. It has no
scheduler or runtime caller and `feature_status()` is unchanged. The host job
still has to locate the manifest-verified ranking bytes, the preserved EGID bytes
and manifest, and the retained calendar digest, then call the publisher.
Tests use artificial fixtures and disposable databases only.

## Host card-report job entrypoint (pending host validation, not scheduled)

`app.scanner_v2_card_report_job.run_egx_card_report_job` (CLI:
`python -m app.scanner_v2_card_report_job --db-path --raw-root --ranking-report
--ranking-sha256 --identity-raw-path --calendar-sha256 --output-dir`) only wires
existing pieces; it adds no evidence store, scanner, risk or admission logic:

1. all paths must be absolute (the identity raw path is the `data_ingestions`
   key, relative to the raw root) and both digests 64 lowercase hex; nothing is
   searched for or defaulted;
2. the database file must already exist as a regular non-symlink file. It is
   never created, initialized or upgraded; a missing schema is
   `DATABASE_UNAVAILABLE`;
3. the EGID manifest comes from `DataIngestionRepository.get_manifest_by_raw_path`
   (provider `egid`, SECURITY_MASTER, size within the identity bound), and its
   bytes from `ImmutableRawStore.read_verified` (size and SHA-256 checked);
4. the ranking bytes are read from one absolute path without following a final
   symlink, regular file only, bounded (oversize is `RANKING_INVALID` downstream);
5. `load_retained_egx_rank_binding` and then `publish_egx_card_report` run
   unchanged; the publisher's own clock is the report `observed_at`.

Input failures raise `CardReportJobError` (`CONFIG_INVALID`,
`DATABASE_UNAVAILABLE`, `RANKING_UNREADABLE`, `IDENTITY_MANIFEST_UNAVAILABLE`,
`IDENTITY_MANIFEST_REJECTED`, `IDENTITY_ARTIFACT_UNREADABLE`); evidence and
publication failures keep their existing errors. The CLI prints one
`scanner-v2-card-report-job-v1` JSON line with `status` PUBLISHED,
ALREADY_CURRENT or FAILED (with `stage` INPUT/EVIDENCE/PUBLICATION and a fixed
reason), always SHADOW / LIVE_MONEY=false / execution disabled /
runtime_activation=false / promotion NOT_AUTHORIZED, and exits non-zero on
failure. Nothing is written before the publisher's own gates pass.

The caller must take the ranking SHA-256 from a verified snapshot manifest and
name the retained calendar digest. The job has no scheduler, cron or systemd
entry here, and the output directory still has to be provisioned on the host
(`EGX_SCANNER_V2_CARD_REPORT_DIR` for the route). Only host evidence of scheduled
successful runs may set the runtime producer. Tests use artificial inputs and
disposable databases only.

### SYSTEM visibility of the producer entrypoint

`feature_status()` now also declares `producer_entrypoint`
(`python -m app.scanner_v2_card_report_job`) and `producer_schedule`
(`NOT_SCHEDULED_IN_REPOSITORY`); SYSTEM renders both rows (missing fields degrade
to NOT_INSTALLED / UNKNOWN). These are code-declared constants: `status` stays
OFFLINE_PROJECTION_NOT_ACTIVATED, `runtime_producer` and `published_cards` stay
null and `runtime_activation` false. A test imports the declared module and
checks that no scheduler/cron/delivery-schedule source under `app/` or `tools/`
names it, so the NOT_SCHEDULED claim fails if a repository schedule is added
without updating the status. Report bytes and the reader are unaffected
(`LIMITATIONS` and the report's `feature_status` constant are unchanged).

## Host evidence-retention job entrypoint (pending host validation, not scheduled)

The card-report job only reads retained evidence, and before this unit nothing
in the repository called the existing producers of that evidence.
`app.scanner_v2_evidence_retention_job.run_egx_evidence_retention_job` (CLI:
`python -m app.scanner_v2_evidence_retention_job --db-path --raw-root --session
--through --identity-raw-path`) wires them and adds no evidence store, calendar
or admission logic:

1. paths must be absolute, `--session`/`--through` exact `YYYY-MM-DD`; the
   database and EGID manifest/bytes are resolved with the card-report job's own
   helpers (existing regular non-symlink database never created/initialized/
   upgraded; provider `egid`, SECURITY_MASTER, bounded, `read_verified`);
2. `SecurityMasterRepository.retain_admission_observation(egid, session,
   manifest SHA-256)` runs unchanged. It requires an already committed admission
   receipt for that exact triple (`IDENTITY_ADMISSION_UNAVAILABLE` otherwise);
3. `TradingRepository.retain_calendar_observation(session, through)` runs
   unchanged over committed `market_sessions` rows.

Both repositories sample their own post-read clock; the job accepts no caller
timestamp, so nothing can be backdated, and exact reruns keep the first
observation. The point-in-time gate stays downstream and unchanged: the
card-report loader rejects evidence observed after the ranking `generated_at`,
so the host must run this job **before** EGX ranking generation for the session.
The printed `calendar_sha256` is what the card-report job's `--calendar-sha256`
expects; the host passes it explicitly.

The CLI prints one `scanner-v2-evidence-retention-job-v1` JSON line: `status`
RETAINED (with identity observation time/receipt digest, calendar digest,
observation time, next session and `calendar_admission` NOT_ESTABLISHED) or
FAILED with `stage` INPUT/IDENTITY/CALENDAR and a fixed reason, always SHADOW /
LIVE_MONEY=false / execution disabled / runtime_activation=false / promotion
NOT_AUTHORIZED; exit code is non-zero on failure. Identity runs first, so a
calendar failure leaves the (immutable, true) identity observation retained; the
calendar step is never reached after an identity failure.

Limitations: a malformed admission row whose payload lacks identity keys
surfaces as `KeyError` from the unchanged repository and is reported as
`IDENTITY_ADMISSION_UNAVAILABLE` rather than a rejection (it still fails
closed). No scheduler/cron entry and no runtime evidence are added; retained
observations establish no admission. Tests use artificial inputs, a fake
security-master retention boundary and disposable databases only.

### SYSTEM visibility of the evidence-retention entrypoint (pending host validation)

`feature_status()` now also declares `evidence_retention_entrypoint`
(`python -m app.scanner_v2_evidence_retention_job`) and
`evidence_retention_schedule` (`NOT_SCHEDULED_IN_REPOSITORY`), so the operator
sees both host jobs the card report depends on. SYSTEM renders both rows; missing
fields degrade to NOT_INSTALLED / UNKNOWN. Code-declared constants only: `status`
stays OFFLINE_PROJECTION_NOT_ACTIVATED, `runtime_producer` and `published_cards`
stay null, `runtime_activation` false. The existing entrypoint test is now
parametrized over both declared modules: each must import with a callable
`main` and runner, and no scheduler/cron/delivery-schedule source under `app/`
or `tools/` may name either module. Report bytes, `LIMITATIONS` and the reader
are unchanged.
