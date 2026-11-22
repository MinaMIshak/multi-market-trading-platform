# News/Event Context — evidence contract (pending host validation)

Status: pure contract, point-in-time read rule, projection, codec, write-once
store library, a separate card-section function, Phase G/H observation,
outcome and metrics libraries, and code-declared SYSTEM stages. No provider, route,
scheduler, Trade Card field or runtime caller. News is evidence enrichment, never a
strategy: it cannot produce BUY/SELL, admission, risk approval or execution.
PAPER/SHADOW only; LIVE_MONEY=DISABLED.

## Existing code inspected

- `app.context.official` already separates OFFICIAL_FACT / REFERENCE_RATE /
  MEDIA_NARRATIVE research-context sections (GDELT headlines are attention, never
  signals). It is a section-level collector, not a per-instrument event record.
- `app.strategies.trade_decision.TradeDecision` fixes `event_risk = 'UNKNOWN'`.
  That is unchanged: no event context is attached to decisions or cards yet.
- `app.strategies.contracts.Contract` (frozen, extra=forbid, strict, no NaN) and
  `aware` are reused; no second contract base.

## `app.news_event` (news-event-v1)

`NewsEvent` keeps four layers separate:

| Layer | Field | Meaning |
|---|---|---|
| source fact | `source` | `source_id`, `source_event_id`, `revision`, retained `artifact_sha256`, `published_at`, `observed_at` (platform retention time), `event_time_status` REPORTED/UNKNOWN with `event_at` only when reported |
| inferred relevance | `association` | MATCHED / AMBIGUOUS / UNRESOLVED / IDENTITY_UNAVAILABLE |
| derived classification | `classification` | one of 15 categories; UNKNOWN iff UNCLASSIFIED; classified records need a classifier version |
| risk interpretation | `risk_interpretation` | fixed NOT_EVALUATED |

Fixed constants: `ingestion_status` RETAINED, `trading_effect` NONE, mode SHADOW,
`live_money` false, `execution_allowed` false. Extra fields (e.g. `signal`) fail.
Nested models are revalidated, so `model_copy` cannot smuggle invalid values.

Timestamps must be timezone-aware; offsets are preserved. `observed_at` may not
precede `published_at`.

Association rules (no guessed identity): only MATCHED names a symbol and canonical
instrument UUID, and it requires basis `EXACT_SOURCE_IDENTIFIER`, confidence
`EXACT` and nonempty mapping evidence references. AMBIGUOUS lists two or more
distinct candidate UUIDs and names none. UNRESOLVED and IDENTITY_UNAVAILABLE carry
no symbol, instrument, candidates or basis. There is no fuzzy basis. The
association market must equal the event market.

`dedup_key` = digest of (contract version, market, source id, source event id):
the same publication across revisions; EGX and US never share a key. `identity`
= digest of the full record, revision included.

## Point-in-time rule

`select_point_in_time(events, *, market, decision_time)`:

- availability is `observed_at <= decision_time`; publication time alone is not
  availability, so items published earlier but retained later are excluded;
- per event, the highest revision available at the decision time is returned; a
  later revision never replaces what an earlier decision saw;
- identical duplicates collapse; two different records for the same event and
  revision that are both available fail closed (order independent); records
  observed later are not inspected, so they cannot change an earlier result;
- naive decision time, unknown market, mixed markets or non-`NewsEvent` inputs
  fail; output is ordered by UTC publication time, source id, source event id.

An empty result is not proof that no relevant event exists.

## Per-instrument context projection (`app.news_event_context`, news-event-context-v1)

Status: pure function, pending host validation. Not attached to TradeDecision
or the Trade Card (`event_risk` stays UNKNOWN); no caller, route or IO.

`project_event_context(events, *, market, instrument_id, decision_time, policy,
coverage_as_of)` returns an `EventContext` for one canonical instrument UUID
(symbol alone is never used). It first applies `select_point_in_time`, then keeps
events published within `policy.lookback_seconds` (inclusive) of decision time.

`EventContextPolicy` is explicit and versioned (`policy_version`,
`lookback_seconds`, `max_coverage_age_seconds`); no implicit defaults.
`coverage_as_of` is when the platform last retained a source pass for the market
at or before decision time; `None` means no retained coverage. A coverage time
after decision time is rejected, not clipped.

Status precedence: UNAVAILABLE (no coverage; no events listed) → STALE
(coverage older than the policy allows; found events still listed) →
EVENT_FOUND (MATCHED to this UUID) → AMBIGUOUS (this UUID is among an AMBIGUOUS
event's candidates) → UNKNOWN (market events in the window could not be
attributed: UNRESOLVED / IDENTITY_UNAVAILABLE) → NO_EVENT_FOUND.

The record lists relevant and ambiguous event identities (sorted SHA-256
digests), the unattributed count and the categories of relevant events only. It
fixes `association_kind` INFERRED, `absence_is_proof` false, `risk_interpretation`
NOT_EVALUATED, `trading_effect` NONE, SHADOW, `live_money` false and
`execution_allowed` false. The validator recomputes the expected status, so an
inconsistent status cannot be constructed.

## Retained byte codec (`app.news_event_record`)

Status: pure codec, pending host validation. No file/network/database IO, store,
provider, route or caller.

`encode_news_event(event)` returns the canonical bytes of one exact, revalidated
`NewsEvent` (sorted keys, compact separators, ASCII, no NaN; bounded at 256 KiB).
These are exactly the bytes hashed by `NewsEvent.identity`, so the SHA-256 of the
retained bytes is the record identity and can be its content address.

`read_retained_news_event(payload, *, expected_sha256)` fails closed with
`RetainedNewsEventError` unless the input is bounded `bytes`, its SHA-256 equals
the lowercase digest recorded by the trusted producer, it is strict JSON (no
duplicate keys, no NaN/Infinity, a JSON object), it validates as an exact
news-event-v1 record (extra fields such as `signal`, naive timestamps,
`live_money` true, any trading effect, cross-market association all fail) and it
re-encodes to identical bytes. Timestamp offsets are preserved. Decoded records
still go through `select_point_in_time`; the codec adds no availability rule.

## Write-once store (`app.news_event_store`, news-event-retention-v1)

Status: library only, pending host validation. No provider, scheduler, route or
caller; the host must provision the directory.

One host-provisioned directory holds `<identity>.json` per exact record (codec
bytes, mode 0444); no pointer, nothing is replaced or deleted. It reuses the
card-report publisher's temp/fsync/link helpers and the loader's no-follow
bounded read.

`retain_news_event(directory, event, *, now=None)` requires an absolute,
non-symlink, non-group/world-writable directory and an exclusive non-blocking
lock (STORE_BUSY). It round-trips the bytes through the codec before writing,
reads every retained record (a damaged record, symlink, directory or unexpected
name fails closed as STORE_INVALID; leftover `.publish-*.tmp` files are
tolerated), returns ALREADY_RETAINED for an exact retry, and otherwise requires
`observed_at` within `MAX_OBSERVATION_LAG` (5 minutes) at or before the store
clock: OBSERVED_IN_FUTURE / OBSERVATION_BACKDATED. Backdating is refused because
it would make a record look available to decisions taken before it was
retained. A different record for the same event and revision is
REVISION_CONFLICT; a later revision is stored beside the original. At most
`MAX_RECORDS` (10000) records. The written bytes are re-read and verified.

`load_retained_news_events(directory, *, market)` returns every record of one
market ordered by identity, or fails closed with no partial content. Callers
still apply `select_point_in_time`; the store adds no availability rule.

## Trade Card event-context section (`app.news_event_card`, trade-card-event-context-v1)

Status: pure function, pending host validation. No IO, route, page, report
field or caller.

`card_event_context(decision, context)` binds one exact `EventContext` to one
exact `TradeDecision` (both revalidated) and returns a separate JSON-ready
section to show beside the card. It fails closed unless market and canonical
instrument UUID match and the context decision time is the same instant as the
decision's detection time (offsets may differ). A context taken later could
carry news retained after the decision, so it cannot be attached to an earlier
card.

The section is labelled `evidence_layer` NEWS_EVENT_INFERENCE,
`observed_market_fact` false and `association_kind` INFERRED. It exposes the
context status, NEWS/EVENT AS OF (`news_event_as_of` = retained coverage time,
null when not configured), coverage, freshness (NOT_CONFIGURED / STALE /
WITHIN_POLICY, derived from the context status), the policy, categories,
relevant/ambiguous event identities (content addresses of retained records),
the unattributed count and a SHA-256 of the canonical context. It fixes
`absence_is_proof` false, `risk_interpretation` NOT_EVALUATED, `trading_effect`
and `plan_effect` NONE, SHADOW, `live_money` and `execution_allowed` false, and
reports `trade_decision_event_risk` as the decision's unchanged UNKNOWN.

`trade_card`, the trade plan, `TradeDecision.event_risk` and the retained card
report bytes are unchanged.

## Per-decision observation record (`app.decision_observation`, decision-observation-v1)

Status: pure function, pending host validation. Phase G groundwork only: no
IO, store, route, scheduler or caller, and no outcome evaluation.

`decision_observation(decision, context, *, recorded_at)` returns one
JSON-ready record for one exact (revalidated) `TradeDecision`: decision
identity, market, symbol, canonical instrument UUID, strategy id/version and
config id, candidate state and SHA-256, detection/data/availability/freshness
times, the immutable plan terms exactly as the decision serializes them
(entry zone and reference, stop, target, valid_until, flat_by,
max_holding_sessions, trigger), risk per share and provenance (source id,
artifact SHA-256, evidence ids). When an `EventContext` is supplied, the
record embeds the exact `card_event_context` section and its canonical
SHA-256 (so all its binding rules apply: same market, instrument and
detection instant); otherwise `event_context.status` is NOT_ATTACHED.

Recording window: `recorded_at` must be aware, at or after detection and
strictly before the source `fresh_until`, `valid_until` and `flat_by`. A
record cannot be written after the decision stopped being current, so the
choice to record cannot depend on later outcomes. The clock is labelled
`CALLER_SUPPLIED_UNVERIFIED`; trusted time and write-once retention are not
provided here.

Fixed: `outcome_status` NOT_OBSERVED, `outcome` null, `evaluation_status`
PENDING_OUTCOME, `performance_claim` NONE, SHADOW, `live_money` and
`execution_allowed` false, `promotion` NOT_AUTHORIZED. `canonical_bytes`
gives deterministic sorted compact ASCII JSON; the same instant in a different
offset gives identical bytes. Cost/slippage assumptions belong to the later
evaluation layer and are not recorded here.

## Observation store (`app.decision_observation_store`, decision-observation-retention-v1)

Status: library, pending host validation. No provisioned directory, producer,
scheduler, route or caller.

`retain_decision_observation(directory, decision, context=None, *, now=None)`
builds the record with the unchanged `decision_observation` function using the
store clock as `recorded_at` (so the recording window is checked at retention
time), and retains `canonical_bytes(record)` as `<sha256>.json` (0444). It
reuses the news-event store's protected directory check, exclusive
non-blocking lock, write-once hard link and no-follow bounded reads; the
directory is never created and records are never replaced or deleted.

One record per decision: a retry whose record differs only in `recorded_at`
returns ALREADY_RETAINED with the FIRST record's identity and time; any other
difference (for example a different attached event context) is
OBSERVATION_CONFLICT. A retry after the window closed is RECORD_REJECTED.
Two records for one decision, damaged, symlinked or unexpected entries fail
closed (STORE_INVALID) for both retention and load.

`read_retained_decision_observation(payload, *, expected_sha256)` accepts only
bounded strict canonical JSON with the producer digest, the exact field set,
the fixed safety values above, SHA-256 decision/candidate ids, EGX/US market,
risk per share equal to entry_reference − stop (> 0), aware timestamps inside
the recording window and, when ATTACHED, an event-context section whose
digest matches and which is bound to the same decision id. It does NOT rebuild
the `TradeDecision` or re-derive `decision_id`; a digest is integrity under a
trusted host writer, not a signature. `load_retained_decision_observations`
returns `(identity, record)` pairs for one market ordered by identity. Records
remain pending observations: no outcome, metric or performance claim.

## Observation producer (`app.decision_observation_producer`, decision-observation-production-v1)

Status: library, pending host validation. No scheduler or route; its only
caller is the host job entrypoint below; the store directory is an explicit
argument.

`produce_egx_decision_observations(directory, artifact, *, expected_sha256,
evidence, now=None)` takes the same retained EGX-RANK-v1 bytes, manifest
digest and `RetainedEgxRankBinding` the card-report publisher uses. It
re-derives the exact `TradeDecision` objects with
`bind_retained_egx_rank_report` (the card report retains cards, not decision
objects, and caller-assembled decisions are never accepted) and applies the
card report's coverage checks. Each decision whose recording window is open
at the producer clock (detection <= now < fresh_until, valid_until, flat_by)
is retained by the unchanged `retain_decision_observation` with
`recorded_at` = producer clock and no event context (NOT_ATTACHED: no news
source or operational EventContextPolicy exists).

Decisions whose window has closed are not retained and are listed one by one
as WINDOW_CLOSED with their decision id; they are never back-filled with a
backdated clock, so a late run is visible. Non-candidate rows are reported
only as the binding's exclusion counts. Reasons: CONFIG_INVALID,
BINDING_REJECTED, CLOCK_BEFORE_GENERATION (clock before ranking generation),
RETENTION_FAILED (carries the store reason). Records retained before a
failure stay (write-once, individually valid); a rerun within the window is
idempotent (ALREADY_RETAINED keeps the first observation). SYSTEM replaces gap
DECISION_OBSERVATION_PRODUCER_NOT_IMPLEMENTED with
DECISION_OBSERVATION_JOB_NOT_IMPLEMENTED (since replaced, see below). No
outcome, performance claim NONE, SHADOW, LIVE_MONEY=false, NOT_AUTHORIZED.

## Observation job (`app.decision_observation_job`, decision-observation-job-v1)

Status: host CLI entrypoint, pending host validation. Not scheduled: there is
no scheduler or cron entry for it in the repository, and the output directory
is not provisioned.

    python -m app.decision_observation_job --db-path /abs/platform.db \
        --raw-root /abs/raw --ranking-report /abs/egx-ranking.json \
        --ranking-sha256 <manifest sha256> --identity-raw-path <EGID raw_path> \
        --calendar-sha256 <retained calendar digest> \
        --output-dir /abs/decision-observations

The arguments are exactly those of `app.scanner_v2_card_report_job`, except
that `--output-dir` names the observation store. The job checks configuration
and reads the database, EGID bytes and ranking bytes with that job's unchanged
helpers (CONFIG_INVALID, DATABASE_UNAVAILABLE, IDENTITY_MANIFEST_*,
IDENTITY_ARTIFACT_UNREADABLE, RANKING_UNREADABLE; the database is never
created or upgraded). It builds the binding with the unchanged
`load_retained_egx_rank_binding` and the same arguments, so card reports and
observations bind the same retained evidence. It then calls the unchanged
producer with the producer's own clock.

Result statuses: PRODUCED (every bound decision RETAINED or ALREADY_RETAINED,
including a ranking with no candidates), WINDOW_CLOSED_PRESENT (run completed,
but at least one decision's window had closed, so it was reported and not
retained; CLI exit 2 so a late run is visible), FAILED (CLI exit 1, stage
INPUT, EVIDENCE or PRODUCTION with the fixed reason). SYSTEM declares the
component and replaces gap DECISION_OBSERVATION_JOB_NOT_IMPLEMENTED with
DECISION_OBSERVATION_JOB_NOT_SCHEDULED; the empirical `scheduled` stage stays
NOT_SCHEDULED_IN_REPOSITORY and the status stays
EMPIRICAL_COLLECTION_NOT_ACTIVE. No outcome, performance claim NONE, SHADOW,
LIVE_MONEY=false, NOT_AUTHORIZED.

## Outcome evaluator (`app.decision_outcome`, decision-outcome-v1)

Status: pure function, pending host validation. No IO, store, route,
scheduler or caller; no aggregate metrics.

`evaluate_decision_outcome(identity, record, bars, *, assumptions,
path_complete_through_at, evaluated_at)` evaluates ONE retained
decision-observation-v1 record. The record is re-encoded and passed through
`read_retained_decision_observation` with its content address, so any edit
fails closed; it is never modified. Bars are a tuple of the existing
`PaperReplayBar` contract (one completed session per bar, same instrument UUID
and symbol). `OutcomeAssumptions` (decision-outcome-assumptions-v1) is explicit
and versioned per market: entry slippage, exit slippage and cost per side in
basis points (exact finite Decimal, 0–1000), labelled DECLARED_UNVALIDATED. The
assumptions market must equal the record market.

Existing engines are not reused for the path because
`simulate_paper_replay` needs a positive M5 risk admission that decisions do not
have (inventing one would fabricate a risk approval), and
`system_candidates.simulate` is the EGX-RANK-v1 two-target lifecycle. The exit
ordering follows the replay engine's conservative rules.

Point in time (ValueError): every bar must begin at or after detection, be
available by `path_complete_through_at` and be in strictly ascending sessions;
`detected_at <= path_complete_through_at <= evaluated_at`; naive clocks fail.
Bars after the exit are never inspected, so later data cannot change a closed
outcome. Path completeness, raw price basis and the clock are caller
assertions and are labelled (`CALLER_ASSERTED_UNVERIFIED`,
`RAW_UNADJUSTED_CALLER_ASSERTED`, `CALLER_SUPPLIED_UNVERIFIED`); corporate
actions are `NOT_CHECKED`.

Rules (`decision-outcome-rules-v1`, listed in every result):

- `LONG_ENTRY_ZONE_TOUCH_V1`: first session ending by `valid_until` that trades
  in the zone; fill at the open clamped to the zone.
- `EGX_RANK_V1_NEXT_SESSION_ZONE_V1`: first supplied session only; open below
  the zone is NO_FILL_GAP_DOWN; open above fills at zone high only if traded
  back, else NO_FILL_GAP_UP.
- Stop before target in one session; on the fill session a target counts only
  if filled at the open or the close is at/above target; gap through stop exits
  at the open, gap through target is capped at target; time exit at the close
  of the `max_holding_sessions`-th session counting the fill session;
  `valid_until` is only the entry deadline.
- A filled plan with `flat_by` is UNEVALUABLE (session bars cannot resolve it).

Statuses: CLOSED (STOP / TARGET / TIME_EXIT) with metrics; NO_ENTRY
(NO_FILL_GAP_DOWN, NO_FILL_GAP_UP, ENTRY_WINDOW_EXPIRED,
NOT_TOUCHED_BEFORE_EXPIRY); PENDING_PATH (AWAITING_ENTRY_WINDOW,
POSITION_OPEN — no partial metrics); UNEVALUABLE
(NO_SESSION_BARS_IN_ENTRY_WINDOW, ENTRY_OUTSIDE_STOP_TARGET after slippage,
FLAT_BY_NOT_SUPPORTED_BY_SESSION_BARS, UNSUPPORTED_ENTRY_TRIGGER).

Metrics per share in planned R (`entry_reference − stop`): gross and net R
(slipped prices minus bps costs on both sides), cost per share, fill risk per
share, MAE/MFE in R (adverse excursion never understated; favourable excursion
excludes prices that may precede an intrabar fill or follow an exit) and
holding sessions. Every result lists the bars used with provenance and fixes
`fill_kind` HYPOTHETICAL_FROM_SESSION_BARS, `execution_evidence` NONE,
`performance_claim` NONE, SHADOW, `live_money`/`execution_allowed` false and
`promotion` NOT_AUTHORIZED. A single outcome is not performance.

## Outcome session-bar binding (`app.decision_outcome_session_bars`, decision-outcome-session-bars-v1)

Status: pure library, pending host validation; no loader, store, route or
scheduler; its only caller is the outcome producer library below.
`bind_egx_outcome_session_bars(identity, record,
rows, sessions, *, received_at)` turns retained evidence, instead of an
arbitrary caller list, into the evaluator's inputs for ONE retained EGX
observation (re-read from its exact bytes): `rows` are `CanonicalDailyBar`s of
one canonical daily artifact (one instrument, provider, snapshot and raw
digest; matching the record's instrument and symbol), `sessions` a contiguous
window of retained `MarketSession` rows (Africa/Cairo) and `received_at` the
ingestion receipt time of the exact daily bytes (caller-asserted).

Completeness: `path_complete_through_at` is the later of the receipt and every
in-path session's verification time; the calendar must start on or before the
detection date and end on the completion date. Each session opening at or
after detection must be WEEKEND (EGX policy) or HOLIDAY with no hours and no
bar, VERIFIED and closed by completion with exactly one VALID_EXECUTABLE bar
received after its close, or (final day only, not VERIFIED) not closed at
completion and without a bar (`pending_session`). Anything else fails closed:
missing or quarantined bars, bars on non-session days or after the calendar,
duplicate dates, mixed artifacts, unestablished session states (UNKNOWN,
DATA_PENDING, ...), sessions closed after the receipt, receipt before
detection, naive clocks. A gap is never skipped. Rows before the detection
date, or for a session that opened before detection, are look-back and ignored.

Bars: `session_id` `XCAI:<date>`, `source_id` the raw digest, `provenance_id`
`<provider>:<snapshot>:row-<n>`; availability rule
AVAILABLE_AT_PATH_COMPLETION_V1 (every bar available at completion). The report
labels the receipt clock CALLER_ASSERTED_UNVERIFIED, calendar admission
NOT_ESTABLISHED, corporate actions NOT_CHECKED, outcome NOT_EVALUATED,
performance claim NONE. The output feeds the unchanged `evaluate_decision_outcome`.

## Outcome producer (`app.decision_outcome_producer`, decision-outcome-production-v1; v2 adds the optional corporate-action gate below)

Status: library only, pending host validation; no job entrypoint, scheduler,
route or caller, no provisioned directory. `produce_egx_decision_outcomes(
observation_directory, outcome_directory, *, daily, sessions, assumptions,
now=None)` composes the unchanged pieces for every retained EGX observation:
`load_retained_decision_observations` → `load_retained_decision_outcomes`
(one exact `OutcomeAssumptions`) → `bind_egx_outcome_session_bars` →
`evaluate_decision_outcome` (`evaluated_at` = producer clock) →
`retain_decision_outcome` (same clock, terminal outcomes only).

- `daily` maps canonical instrument UUID strings to `EgxDailyEvidence(rows,
  received_at)` (retained canonical daily rows of one artifact and the
  caller-asserted receipt time of those bytes); `sessions` is a tuple of
  retained `MarketSession` rows. Rule CALENDAR_THROUGH_RECEIPT_DATE_V1: each
  observation is offered the sessions from its Cairo detection date through
  the receipt date, so sessions after the receipt never enter the path.
- Refused as CONFIG_INVALID: relative or identical store directories,
  non-EGX or inexact assumptions, non-tuple or inexact sessions, malformed
  daily mapping, naive receipt time, receipt after the producer clock.
  Damaged stores: OBSERVATIONS_UNAVAILABLE / OUTCOMES_UNAVAILABLE. Any outcome
  store refusal aborts the run (RETENTION_FAILED); earlier write-once outcomes
  stay and reruns are idempotent.
- One entry per observation, none dropped: RETAINED, ALREADY_RETAINED (an
  existing outcome is not re-evaluated; `retained_outcome_reevaluation`
  NOT_PERFORMED), PENDING_PATH (never retained), AWAITING_DAILY_EVIDENCE
  (NO_DAILY_EVIDENCE / RECEIVED_BEFORE_DETECTION), OBSERVATION_AFTER_CLOCK,
  BINDING_REJECTED and EVALUATION_REJECTED (fail-closed message in `detail`).
  Decisions without a retained outcome stay NOT_EVALUATED in the unchanged
  aggregate, so a gap cannot improve a segment. Retained outcomes whose
  observation is absent are counted.

Labels: receipt clock CALLER_ASSERTED_UNVERIFIED, calendar admission
NOT_ESTABLISHED, corporate actions NOT_CHECKED, HYPOTHETICAL_FROM_SESSION_BARS,
execution evidence NONE, performance claim NONE, SHADOW, LIVE_MONEY=false,
NOT_AUTHORIZED. SYSTEM declares the component, replaces gap
OUTCOME_PRODUCER_NOT_IMPLEMENTED with OUTCOME_JOB_NOT_IMPLEMENTED and adds
OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED.

## Outcome daily evidence loader (`app.decision_outcome_daily_evidence`, decision-outcome-daily-evidence-v1)

Status: library, pending host validation; its only caller is the outcome job
below (not scheduled); no route. `load_egx_daily_evidence(database, raw_root, raw_paths)` builds
the producer's `daily` input from a tuple of exact `data_ingestions.raw_path`
values named by the caller (nothing searched for, defaulted or chosen as
"latest"):

- each path is resolved with the unchanged `get_manifest_by_raw_path`; the
  ingestion must be DAILY_BARS/D1, RECEIVED or VALIDATED, carry a source URI
  and the ingestor's metadata (canonical lowercase instrument UUID, canonical
  symbol equal to the manifest symbol, snapshot date equal to the manifest
  date, requested start <= end <= snapshot);
- the exact bytes are verified with `ImmutableRawStore.read_verified` and
  canonicalized by the unchanged `DailyCanonicalPipeline.canonicalize_ingestion`;
  every row must lie inside the requested dates; quarantined rows pass through
  (the session-bar binding rejects them on an outcome path);
- `received_at` is the ingestion manifest's receipt time, labelled
  INGESTION_MANIFEST_UNVERIFIED.

Refusals (whole load, no partial mapping): CONFIG_INVALID (relative raw root,
non-tuple, empty, over 1000 or duplicate paths), DATABASE_UNAVAILABLE,
INGESTION_UNAVAILABLE, INGESTION_REJECTED, ARTIFACT_UNREADABLE,
CANONICALIZATION_REJECTED, DUPLICATE_INSTRUMENT (two named ingestions for one
instrument are refused, never resolved by preference). The database is only
read. Returns `daily`, one provenance entry per path in order, and labels market
membership NOT_ESTABLISHED (the binding re-checks instrument and symbol against
each EGX record), corporate actions NOT_CHECKED, performance claim NONE,
SHADOW, LIVE_MONEY=false, NOT_AUTHORIZED. SYSTEM declares the component.

## Outcome host job (`app.decision_outcome_job`, decision-outcome-job-v5)

Status: host CLI entrypoint, pending host validation; not scheduled, no route,
no provisioned directory. `python -m app.decision_outcome_job` takes
`--db-path`, `--raw-root`, `--observation-dir`, `--outcome-dir`, one or more
`--daily-raw-path`, one or more `--calendar-observation` (exact
`calendar_observations` SHA-256 digests, 1..400, unique, lowercase) and
explicit `--assumptions-version`, `--entry-slippage-bps`,
`--exit-slippage-bps`, `--cost-bps-per-side` (no default costs or slippage).
It only wires unchanged pieces:

1. the existing database file is opened with the card-report job's
   `_existing_database` (never created or upgraded; read only);
2. one job clock is sampled and used both as the calendar chain's `as_of` and
   as the producer clock;
3. `load_egx_calendar_observations` reads exactly the named write-once
   `calendar_observations` rows and `chain_egx_calendar_observations`
   re-validates each at its own observation time and chains them (missing
   link CHAIN_NOT_CONTIGUOUS, duplicate session, observed after the job clock,
   tampered or unknown rows all refuse the run at stage CALENDAR with the
   loader's or chain's reason); `market_sessions` is never read;
4. `load_egx_daily_evidence` loads exactly the named raw paths;
5. `produce_egx_decision_outcomes` binds, evaluates and retains terminal
   outcomes with the job clock.

decision-outcome-job-v1 read current, upserted `market_sessions` rows by date
range (labelled MUTABLE_NOT_RETAINED, gap OUTCOME_CALENDAR_FROM_MUTABLE_SESSIONS).
v2 labels the calendar CALENDAR_OBSERVATIONS_RETAINED / WRITE_ONCE_RETAINED,
returns the chain report (including `sessions_sha256` and `as_of`) and
per-digest provenance with observed-at clock REPOSITORY_CLOCK_UNVERIFIED and
admission NOT_ESTABLISHED, and SYSTEM drops that gap. Statuses: PRODUCED
(exit 0; no observation rejected, waiting states PENDING_PATH and
AWAITING_DAILY_EVIDENCE allowed), REJECTIONS_PRESENT (exit 2; at least one
OBSERVATION_AFTER_CLOCK, BINDING_REJECTED or EVALUATION_REJECTED), FAILED
(exit 1; stage INPUT, CALENDAR, DAILY_EVIDENCE or PRODUCTION with a fixed
reason). The result carries daily-evidence provenance per raw path, corporate
actions NOT_CHECKED, HYPOTHETICAL_FROM_SESSION_BARS, execution evidence NONE,
performance claim NONE, SHADOW, LIVE_MONEY=false, NOT_AUTHORIZED. SYSTEM
declares the component and keeps gap OUTCOME_JOB_NOT_SCHEDULED. A host must
name digests of observations that the scanner-v2 evidence retention job
actually retained; a chain can cover only those sessions.

v3 adds only an opt-in `--check-corporate-actions` (default off: the run is
exactly v2, `action_evidence` is null, corporate actions NOT_CHECKED). With it
the job builds one request per observation that can reach the producer's
corporate-action gate (OBSERVATIONS_REACHABLE_BY_PRODUCER_ACTION_GATE_V1:
recorded at or before the job clock, no retained outcome under the run's
assumptions, daily evidence for its instrument received at or after detection
and not after the clock) with `path_through` = the Africa/Cairo date of that
receipt (DAILY_RECEIPT_DATE_CAIRO_V1, the last date the producer's
CALENDAR_THROUGH_RECEIPT_DATE_V1 window can offer, so coverage is required
through it: stricter than the path-completion date the check uses, never
looser). The unchanged `load_egx_outcome_action_evidence_by_request` selects
point-in-time at the job clock; its selections go unchanged to the producer,
which retains a terminal outcome only on CLEAR. A refused request is
AWAITING_ACTION_EVIDENCE and ACTIONS_IN_PATH is not retained (both exit 0);
ACTION_CHECK_REJECTED is a rejection (exit 2); a whole-load refusal
(CONFIG_INVALID, including more than MAX_REQUESTS requests, or
DATABASE_UNAVAILABLE) fails the run at the new stage ACTION_EVIDENCE before
production. No request means the loader is not called and the producer gets an
empty mapping. The result's `action_evidence` summarises the loader's counts,
per-request provenance and its database-write label (the unchanged
ReferenceRepository may write its audit row for refused requests); top-level
`corporate_actions` is the producer's label. Retained outcome records still do
not carry the check, so SYSTEM keeps gap OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED.
No price is adjusted. Pending host validation.

v4 adds only an opt-in `--attestation-dir` (`attestation_directory=`),
accepted only together with `--check-corporate-actions`, absolute and distinct
from the observation and outcome stores (else CONFIG_INVALID at stage INPUT,
before the database is opened). Default off: the run is exactly v3
(`outcome_attestation` NOT_REQUESTED). With it the directory is passed
unchanged to the producer's decision-outcome-production-v3 attestation step:
each outcome this run retains after a CLEAR check gets one write-once sidecar
attestation (AT_EVALUATION, same job clock). The job itself builds, loads,
verifies and partitions no attestation. A missing or damaged sidecar fails the
run at stage PRODUCTION (ATTESTATIONS_UNAVAILABLE) before any outcome or
attestation is written; a sidecar store refusal fails it at PRODUCTION
(ATTESTATION_FAILED, earlier write-once records kept). ATTESTATION_REJECTED and
RETAINED_OUTCOME_DIFFERS (outcome retained but UNATTESTED) count as rejections
(exit 2). Without v5's `--attest-held-outcomes`, outcomes already held when
the run starts are neither re-checked nor attested and stay UNATTESTED (never
clear). Top-level `outcome_attestation` is the producer's label. No directory
is provisioned and nothing reads attestations for metrics, so SYSTEM keeps gaps
OUTCOME_ATTESTATION_STORE_NOT_PROVISIONED and
OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED. Committed at 163411b.

v5 adds only an opt-in `--attest-held-outcomes` (`attest_held_outcomes=`,
a bool), accepted only together with `--attestation-dir` (which itself requires
`--check-corporate-actions`); otherwise CONFIG_INVALID at stage INPUT before
the database is opened. Default off: the run is exactly v4
(`held_outcome_attestation` NOT_PERFORMED; the producer gets
`attest_held_outcomes=False`). With it the job also requests the retained
egx-actions-v1 selection for every observation whose terminal outcome is
already held under the run's assumptions
(OUTCOMES_HELD_UNDER_THESE_ASSUMPTIONS_V1) with `path_through` = the
Africa/Cairo date of that held outcome's own `path_complete_through_at`
(HELD_PATH_COMPLETION_DATE_CAIRO_V1: exactly the window the producer's
HELD_RECHECK_AFTER_EVALUATION_V1 re-check and the attestation builder require),
through the same single by-request loader call at the job clock, and passes the
flag unchanged to the producer (decision-outcome-production-v4). Only a CLEAR
re-check is attested (check_timing AFTER_EVALUATION) by the unchanged builder
and store; held outcomes are never re-evaluated, re-retained, replaced or
price-adjusted (entry status stays ALREADY_RETAINED). HELD_AWAITING_ACTION_EVIDENCE
and HELD_ACTIONS_IN_PATH stay UNATTESTED and are not rejections (exit 0);
HELD_CHECK_REJECTED (the check failed closed) is a rejection (exit 2). The job
reads no attestation, so every held outcome is requested on every such run;
re-attesting an already attested outcome is the store's ALREADY_RETAINED. New
plus held requests above the loader's MAX_REQUESTS, or a job clock whose Cairo
date is before a held path date, fail the run at ACTION_EVIDENCE (the unchanged
loader's CONFIG_INVALID) rather than checking a subset. `action_evidence` adds
`held_request_scope` / `held_path_through_rule` (null when off); top-level
`held_outcome_attestation` is the producer's label. Nothing reads attestations
for metrics and nothing runs the job, so SYSTEM keeps gaps
OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED, OUTCOME_ATTESTATION_STORE_NOT_PROVISIONED
and OUTCOME_JOB_NOT_SCHEDULED. Written by the file-only Claude paced executor
without running tests; pending host validation.

## Retained outcome calendar chain (`app.decision_outcome_calendar`, decision-outcome-calendar-chain-v1)

Status: pure library, pending host validation; only caller is the outcome
job (decision-outcome-job-v2).
`chain_egx_calendar_observations(observations, *, as_of)` takes a tuple of
`RetainedCalendarObservation` (artifact SHA-256, session date, exact bytes,
aware observed_at: the columns of the write-once `calendar_observations`
table) and returns a contiguous `MarketSession` tuple for the unchanged
producer/binding plus a JSON-ready report.

- Each observation is re-validated by the unchanged `project_egx_calendar`
  with `decision_time` = its own `observed_at` (digest, contiguity, EGX weekend
  policy, hours, completed-session verification at observation, observed
  before the next session opened); failures are OBSERVATION_REJECTED.
- Ordered by session (input order irrelevant); two observations for one
  session are DUPLICATE_SESSION (never resolved by preference); each
  `next_session` must equal the next observation's session
  (CHAIN_NOT_CONTIGUOUS), so no trading day is skipped; observed after `as_of`
  is OBSERVED_AFTER_CLOCK; more than 400 days CHAIN_TOO_LONG; bad types
  CONFIG_INVALID.
- Each observation contributes its VERIFIED session and intervening
  non-trading rows; an UNKNOWN schedule row is superseded by the later
  VERIFIED row for that date, and a changed schedule (opened/first-15/closed)
  is listed in `schedule_revisions`. Only the last observation's UNKNOWN row is
  kept, as the final pending day.
- Report: dates, days, `sessions_sha256` (same formula as the v1 job's calendar
  digest), per-observation digest/session/next_session/observed_at, latest
  observed_at, labels CALENDAR_OBSERVATIONS_RETAINED / WRITE_ONCE_RETAINED,
  admission NOT_ESTABLISHED, outcome NOT_EVALUATED, performance claim NONE,
  SHADOW, LIVE_MONEY=false, NOT_AUTHORIZED.

In this repository only the scanner-v2 evidence retention job calls
`retain_calendar_observation`, so a chain can exist only for sessions that job
retained when it ran; a missing link refuses the chain.

## Retained calendar observation loader (`app.decision_outcome_calendar_loader`, decision-outcome-calendar-observations-v1)

Status: read-only library, pending host validation; only caller is the
outcome job (decision-outcome-job-v2).
`TradingRepository` only offers a projection getter for one digest, so
`load_egx_calendar_observations(database, artifact_digests)` reads the exact
`calendar_observations` rows the chain needs.

- The caller names a tuple of 1..400 unique lowercase SHA-256 digests; nothing
  is searched for, ranged, defaulted or chosen as latest (CONFIG_INVALID
  otherwise). Only `calendar_observations` is read; `market_sessions` is never
  read, so a later upsert cannot change what is loaded.
- Each row must exist (OBSERVATION_UNAVAILABLE); its artifact must be a BLOB of
  1..64 KiB whose SHA-256 equals the named digest, `session` an exact ISO date
  and `observed_at` an ISO timestamp with an offset (naive refused); otherwise
  OBSERVATION_REJECTED. SQLite/OS failures are DATABASE_UNAVAILABLE. Any
  failure refuses the whole load.
- Returns `RetainedCalendarObservation` values in the caller's order (the
  unchanged chain orders and validates them) plus per-digest provenance
  (stored observed_at text, byte length); labels CALLER_NAMED_DIGESTS,
  CALENDAR_OBSERVATIONS_RETAINED / WRITE_ONCE_RETAINED,
  REPOSITORY_CLOCK_UNVERIFIED, calendar validation NOT_PERFORMED_BY_LOADER,
  admission NOT_ESTABLISHED, performance claim NONE, SHADOW, LIVE_MONEY=false,
  NOT_AUTHORIZED. The database is never created (a missing file is refused
  before any connection), initialized or written.

## Outcome-path corporate-action check (`app.decision_outcome_corporate_actions`, decision-outcome-corporate-actions-v1)

Status: pure library, pending host validation; no caller. The outcome path
compares raw canonical OHLC with plan prices fixed as of `data_as_of`, so an
action effective inside the path makes the outcome non-evidence.
`check_egx_outcome_corporate_actions(identity, record, evidence, *,
source_ingestion_id, review_ingestion_ids, path_through, as_of)` checks one
retained EGX observation against one exact `egx-actions-v1` `ActionEvidence`
document, which is the complete, bounded, reviewed coverage that the existing
`ReferenceRepository.actions` selects point-in-time.

- Window DATA_AS_OF_DATE_THROUGH_PATH_DATE_INCLUSIVE_V1: Africa/Cairo dates
  from the record's `data_as_of` date through `path_through` inclusive. The
  `data_as_of` date is included on purpose, so the check errs toward
  ACTIONS_IN_PATH.
- Fails closed (ValueError) on an edited or non-EGX record, evidence for
  another instrument, coverage that does not span the whole window (an absent
  action outside declared coverage is never evidence), evidence published
  after `as_of`, a path date before detection or after `as_of`, a
  non-canonical or duplicate source/review UUID, a review id equal to the
  source id, and naive clocks. The evidence is revalidated, so a
  `model_construct` bypass is rejected.
- Result CLEAR (with `clear_basis` COMPLETE_DECLARED_COVERAGE) or
  ACTIONS_IN_PATH, listing every in-window action of any type in date order
  with its declared adjustment effect. Nothing is adjusted
  (`price_adjustment` NOT_APPLIED) and no outcome is evaluated, changed or
  retained. Review ids are labelled CALLER_ASSERTED_UNVERIFIED. Performance
  claim NONE, SHADOW, LIVE_MONEY=false, NOT_AUTHORIZED.
- The evaluator, binding, producer, store and job are unchanged and still
  label corporate actions NOT_CHECKED. Gap OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED
  stays open until a loader and the outcome path use this check, making an
  ACTIONS_IN_PATH path or missing coverage visible as non-evidence.

## Outcome-path action evidence loader (`app.decision_outcome_action_evidence`, decision-outcome-action-evidence-v1)

Status: library, pending host validation; no caller. It supplies the check's
`evidence`, `source_ingestion_id` and `review_ingestion_ids` inputs and adds no
selection, review or check logic of its own.
`load_egx_outcome_action_evidence(database, raw_root, requests, *, as_of)`
takes a tuple of caller-named (observation identity, retained record,
`path_through`) triples.

- Each record is re-read from its exact bytes by the check's own reader (content
  address, EGX). The window is the check's
  DATA_AS_OF_DATE_THROUGH_PATH_DATE_INCLUSIVE_V1 rule, and `path_through` must
  lie between the detection date and the `as_of` date (else CONFIG_INVALID).
- Selection is made only by the unchanged `ReferenceRepository.actions`:
  exactly one validated, reviewed egx-actions-v1 document received, completed
  and published by `as_of` whose coverage spans the window. Missing, partial,
  overlapping, unreviewed or damaged evidence is ACTIONS_UNAVAILABLE and is
  never resolved by preference. The returned document, source id and review ids
  are re-checked (ACTIONS_REJECTED), never repaired.
- Whole load refused on any failure (CONFIG_INVALID, DATABASE_UNAVAILABLE,
  ACTIONS_UNAVAILABLE, ACTIONS_REJECTED). A missing database file is refused
  before any connection, so nothing is created. The module issues no SQL of
  its own and nothing is written on success. On refusal the unchanged
  repository may record its existing `data_quality_issues` audit row.
- Result: per-identity `OutcomeActionSelection` plus per-request provenance
  (window, raw path/digest, provider, receipt/completion, coverage,
  publication, review ids) in caller order. It is labelled
  `corporate_action_check` NOT_PERFORMED_BY_LOADER, review verification
  REFERENCE_REPOSITORY_REQUIRE_REVIEW, reviewer authority NOT_ATTESTED, price
  adjustment NOT_APPLIED, outcome NOT_EVALUATED, performance claim NONE,
  SHADOW, LIVE_MONEY=false and NOT_AUTHORIZED.
- The check is not called here. The job, evaluator and store are unchanged,
  so gap OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays open.

### Per-request refusal (decision-outcome-action-evidence-by-request-v1)

Status: library function, pending host validation; no caller.
`load_egx_outcome_action_evidence_by_request(database, raw_root, requests, *, as_of)`
has the same arguments, request checks and per-request selection as the v1
loader, so one observation's missing evidence no longer blocks every other
observation.

- ACTIONS_UNAVAILABLE and ACTIONS_REJECTED are reported on that request's own
  `sources` entry (`status`, `detail`; every selection field None; identity
  and window kept) and the request gets no selection. Fed to the producer
  gate, that observation becomes AWAITING_ACTION_EVIDENCE and nothing is
  retained for it. Other evidence is never substituted.
- CONFIG_INVALID (checked for every request before the database is opened) and
  DATABASE_UNAVAILABLE (missing file, SQLite error) still refuse the whole
  load.
- Every entry carries `status` SELECTED / ACTIONS_UNAVAILABLE /
  ACTIONS_REJECTED, and `request_counts` reports totals. `database_writes` is
  NONE when nothing was refused; otherwise it is
  REPOSITORY_AUDIT_ROWS_POSSIBLE_FOR_REFUSED_REQUESTS, because the unchanged
  repository may record its audit row. Labels otherwise match v1, plus
  `refusal_scope` PER_REQUEST_EVIDENCE_ONLY and `refused_request_effect`
  NO_SELECTION_FOR_REQUEST.
- The v1 function's behaviour and output are unchanged. The job still passes
  no selections, so gap OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays open.
  Still open before the job can use this: which `path_through` the job names
  per observation. The producer checks through the path-completion date, which
  is known only after binding, and the repository treats any overlapping
  document as ambiguous, so a later path date can refuse where the exact one
  would not.

## Producer corporate-action gate (decision-outcome-production-v2)

Status: library change, pending host validation; the job does not use it yet.
`produce_egx_decision_outcomes(..., now=None, actions=None)` gains an optional
`actions` mapping (observation identity → exact `OutcomeActionSelection`, the
shape the action-evidence loader returns; anything else CONFIG_INVALID).

- Without `actions` the v1 behaviour is unchanged and the run says
  `corporate_actions` NOT_CHECKED.
- With `actions`, each terminal outcome (CLOSED, NO_ENTRY, UNEVALUABLE) is
  passed, before retention, to the unchanged pure
  `check_egx_outcome_corporate_actions` with `path_through` = the Cairo date of
  the binding's `path_complete_through_at` (PATH_COMPLETION_DATE_CAIRO_V1, the
  calendar's final date) and `as_of` = the producer clock. Only CLEAR is
  retained. ACTIONS_IN_PATH (full check report in the entry),
  AWAITING_ACTION_EVIDENCE / NO_ACTION_EVIDENCE (no selection) and
  ACTION_CHECK_REJECTED (fail-closed message) are reported per observation and
  never retained, so the unchanged aggregate counts them NOT_EVALUATED. The
  hypothetical outcome status stays visible in the entry. PENDING_PATH is not
  checked. The run says CHECKED_BEFORE_RETENTION, gives the window and path
  rules and counts `action_selections_without_observation`.
- No price is adjusted (`price_adjustment` NOT_APPLIED). Already retained
  outcomes are not re-checked, and the retained decision-outcome-v1 record does
  not carry the check (`retained_outcome_corporate_actions`
  NOT_RECORDED_IN_OUTCOME), so checked and unchecked retained outcomes cannot
  be told apart in the store. Gap OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED
  therefore stays open.

## Producer attestation step (decision-outcome-production-v3)

Status: library change written by the file-only Claude paced executor, tests
NOT run in that cycle; committed at 7a95d84. The job passes the new argument
only with its opt-in `--attestation-dir` (decision-outcome-job-v4, above).
`produce_egx_decision_outcomes(..., actions=None,
attestation_directory=None)`:

- Without `attestation_directory` the v2 behaviour is unchanged
  (`outcome_attestation` NOT_REQUESTED, `attestation_counts` null, entry
  attestation fields null).
- `attestation_directory` requires `actions` (only CLEAR is attestable) and
  must be an absolute directory distinct from the observation and outcome
  stores (else CONFIG_INVALID). The sidecar is loaded (structure only) before
  any evaluation; a missing or damaged sidecar refuses the run before any write
  (ATTESTATIONS_UNAVAILABLE).
- For an outcome this run retained after a CLEAR check (outcome-store status
  RETAINED, or ALREADY_RETAINED with exactly this run's bytes), the unchanged
  `attest_outcome_corporate_actions` binds the retained outcome, its
  observation and the entry's exact CLEAR report, and the unchanged
  `retain_outcome_action_attestation` retains it with the same clock (so
  `check_timing` is AT_EVALUATION). Entry fields `attestation_status`
  (RETAINED / ALREADY_RETAINED / ATTESTATION_REJECTED /
  RETAINED_OUTCOME_DIFFERS), `attestation_identity`, `attestation_detail`;
  run-level `attestation_counts`.
- A builder refusal is per observation (ATTESTATION_REJECTED; the outcome stays
  retained and UNATTESTED). A sidecar store refusal aborts the run
  (ATTESTATION_FAILED); outcomes and attestations already written stay.
- Without the v4 opt-in below, outcomes held before the run starts are neither
  re-checked nor attested (`held_outcome_attestation` NOT_PERFORMED). An
  outcome retained by an unchecked run, or whose attestation failed, therefore
  stays UNATTESTED for the partition (never clear).
- The outcome record is unchanged (`retained_outcome_corporate_actions`
  NOT_RECORDED_IN_OUTCOME), no price is adjusted, labels SHADOW,
  LIVE_MONEY=false, NOT_AUTHORIZED. Gap OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED
  stays open: nothing calls the partition.

## Held-outcome re-check (decision-outcome-production-v4)

Status: library change written by the file-only Claude paced executor; tests
NOT run in that cycle; committed at 25fc4bb. The job passes `True` only with its
opt-in `--attest-held-outcomes` (decision-outcome-job-v5, above), which also
requests action selections for held outcomes through their own path dates.
`produce_egx_decision_outcomes(..., attestation_directory=None,
attest_held_outcomes=False)`:

- `attest_held_outcomes` must be a bool and `True` requires
  `attestation_directory` (which requires `actions`), else CONFIG_INVALID.
- Rule HELD_RECHECK_AFTER_EVALUATION_V1: for each observation whose outcome is
  already in the store when the run starts, the unchanged
  `check_egx_outcome_corporate_actions` runs over that held outcome's own path
  (`path_through` = Africa/Cairo date of its `path_complete_through_at`,
  PATH_COMPLETION_DATE_CAIRO_V1) with `as_of` = the producer clock. Only CLEAR
  is attested, by the unchanged builder and sidecar store of v3
  (`check_timing` AFTER_EVALUATION when the clock is later than the held
  evaluation; an earlier clock is refused, ATTESTATION_REJECTED or
  HELD_CHECK_REJECTED). A later re-check with the same evidence is
  ALREADY_RETAINED (first attestation kept); different evidence is the store's
  ATTESTATION_CONFLICT, which aborts the run (ATTESTATION_FAILED).
- New per-entry `attestation_status` values HELD_AWAITING_ACTION_EVIDENCE (no
  selection), HELD_ACTIONS_IN_PATH (full report in `corporate_action_check`)
  and HELD_CHECK_REJECTED (fail-closed message in `attestation_detail`) leave
  the held outcome UNATTESTED (never clear). Run-level
  `held_outcome_attestation` is HELD_RECHECK_AFTER_EVALUATION_V1 (else
  NOT_PERFORMED) and `held_outcomes_rechecked` the number of held entries
  re-checked (else null).
- A held outcome is never re-evaluated, re-retained, replaced or
  price-adjusted; its entry status stays ALREADY_RETAINED and the outcome store
  is not written. PAPER/SHADOW, LIVE_MONEY=false, NOT_AUTHORIZED. Gap
  OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays open (a metrics reader of
  verified attestations remains).

## Outcome action attestation (`app.decision_outcome_action_attestation`, decision-outcome-action-attestation-v1)

Status: pure library, pending host validation; nothing builds, retains or reads
it yet. It is the record that will let checked outcomes be told apart from
unchecked ones without changing decision-outcome-v1, the outcome store or the
aggregate (whose exact outcome field set stays as is): a separate attestation
next to the retained outcome rather than an outcome version bump.

`attest_outcome_corporate_actions(outcome_identity, outcome, observation, check)`
binds:

- one retained terminal outcome by content address (re-read through the
  unchanged `read_retained_decision_outcome` and re-bound to its observation by
  the unchanged `decision_outcome_metrics._check_outcome`);
- the retained EGX decision-observation-v1 `(identity, record)` it was
  evaluated for (re-read from exact bytes);
- one exact CLEAR decision-outcome-corporate-actions-v1 report, structurally
  re-checked: exact field sets, fixed safety labels, no action in path,
  COMPLETE_DECLARED_COVERAGE, same observation/decision/symbol/instrument,
  window rule DATA_AS_OF_DATE_THROUGH_PATH_DATE_INCLUSIVE_V1 from the record's
  Cairo `data_as_of` date to the Cairo date of the outcome's
  `path_complete_through_at` (PATH_COMPLETION_DATE_CAIRO_V1, the producer's
  rule; a shorter or longer window is refused), complete egx-actions-v1 coverage
  spanning it, evidence published by the check's `as_of`, canonical
  source/review UUIDs (reviews sorted, unique, not the source), and check
  `as_of` not before the outcome's `evaluated_at`.

ACTIONS_IN_PATH is never attestable. The record carries outcome/observation
identities, strategy segment, assumptions version, outcome status and times,
both rules, `corporate_action_status` CLEAR, `check_as_of`, `check_timing`
(AT_EVALUATION / AFTER_EVALUATION), the embedded check and its canonical
SHA-256, `check_recomputation` NOT_PERFORMED (the evidence document is not
embedded), review ids CALLER_ASSERTED_UNVERIFIED, `price_adjustment`
NOT_APPLIED, HYPOTHETICAL_FROM_SESSION_BARS, execution evidence NONE,
performance claim NONE, SHADOW, LIVE_MONEY=false, NOT_AUTHORIZED.
`verify_outcome_action_attestation` rebuilds it from the embedded check and
compares canonical bytes, so an edited, extended or re-pointed attestation is
refused. SYSTEM declares the component; gap OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED
stays open until the producer retains attestations (write-once, beside the
outcomes; library step added in decision-outcome-production-v3, not enabled by
the job) and a loader restricts metrics to attested outcomes.

## Outcome attestation store (`app.decision_outcome_attestation_store`, decision-outcome-attestation-retention-v1)

Status: library only, pending host validation; no directory provisioned
(gap OUTCOME_ATTESTATION_STORE_NOT_PROVISIONED), no producer, job, scheduler,
route or caller. A write-once SIDECAR beside the outcome store (a separate
directory; an outcome record in it fails closed on scan).
`retain_outcome_action_attestation(directory, attestation, outcome,
observation, *, now=None)` takes the retained `(identity, record)` outcome and
observation pairs, re-derives the attestation with the unchanged
`verify_outcome_action_attestation` and refuses a check `as_of` after the store
clock (RECORD_REJECTED) before any write. One attestation per outcome
identity: an exact retry, or one differing only in check time (`check_as_of`,
`check_timing`, the embedded check's `as_of`, `check_sha256`), is
ALREADY_RETAINED with the first record; any other difference (evidence,
coverage, reviews) is ATTESTATION_CONFLICT. Same layout, lock, write-once and
post-write verification as the outcome store; result labels
`store_clock` CALLER_SUPPLIED_UNVERIFIED, NOT_APPLIED, HYPOTHETICAL_FROM_SESSION_BARS,
NONE, SHADOW, LIVE_MONEY=false, NOT_AUTHORIZED.
`read_retained_outcome_attestation` / `load_retained_outcome_attestations`
check structure only (field set, fixed labels, terminal outcome status, check
timing consistency, embedded check digest and identity fields); a loaded
attestation is not evidence until re-verified against its retained outcome and
observation, and a missing attestation means unattested, never clear. Gap
OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays open until the producer retains
attestations and a loader restricts metrics to verified attested outcomes.

## Outcome attestation partition (`app.decision_outcome_attested`, decision-outcome-attestation-partition-v1)

`partition_egx_attested_outcomes(population, outcomes, attestations)` is the
pure reader over what the unchanged loaders return (observation pairs, outcome
records, attestation records). It re-reads every observation and outcome at its
own content address (EGX only), re-binds every outcome to its population
observation with the unchanged metrics check, re-reads every attestation with
the unchanged store reader, and fails closed (`ValueError`) on duplicates, an
outcome without its observation or two attestations for one outcome. Each
outcome is then exactly one of ATTESTED_CLEAR (its attestation passes the
unchanged `verify_outcome_action_attestation` against this outcome and
observation), UNATTESTED (none supplied: NOT_CHECKED_NOT_CLEAR,
`absence_is_proof` false) or ATTESTATION_INVALID (names the outcome but does not
verify; visible, never attested). Counts always add up to the outcomes
supplied; attestations naming an outcome not supplied are listed separately.
`attested_clear_outcomes` returns fresh copies of only the ATTESTED_CLEAR
records for `aggregate_decision_outcomes` with the same population, where every
other record counts NOT_EVALUATED, so restricting to checked outcomes cannot
hide a loss. The check is not re-run; no IO, write or route; its only caller is
the pure checked-metrics component below. Gap
OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays open until a job runs that caller.

## Checked outcome metrics (`app.decision_outcome_checked_metrics`, decision-outcome-checked-metrics-v1)

Status: pure function, pending host validation; no job, route, scheduler or
store read. `aggregate_egx_checked_decision_outcomes(population, outcomes,
attestations, *, assumptions, as_of)` composes the unchanged components:

1. `aggregate_decision_outcomes` over ALL supplied outcomes as an integrity
   gate only (market, exact assumptions, binding, times by `as_of`); its
   result is discarded (`unrestricted_metrics: NOT_REPORTED`);
2. `partition_egx_attested_outcomes` over the same inputs;
3. point-in-time rule CHECK_AS_OF_AT_OR_BEFORE_AS_OF_V1: an ATTESTED_CLEAR
   outcome counts only if its attestation's `check_as_of` is at or before
   `as_of`; a later check is ATTESTED_CLEAR_AFTER_AS_OF and is listed, not
   counted, so a later attestation cannot change an earlier report (the
   attestation's own retention time is not recorded and not checked);
4. `aggregate_decision_outcomes` again with the SAME population and only the
   counted outcomes, so every UNATTESTED, ATTESTATION_INVALID, late-attested or
   missing outcome is NOT_EVALUATED there and cannot hide a loss.

The report embeds the partition and the restricted metrics with sample size,
labels population and attestation completeness CALLER_ASSERTED_UNVERIFIED and
makes no performance claim. Gap OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays
open: its only caller is the unscheduled read-only metrics job below.

## Checked metrics host job (`app.decision_outcome_metrics_job`, decision-outcome-metrics-job-v1)

Status: host CLI entrypoint, pending host validation; not scheduled, no route.

    python -m app.decision_outcome_metrics_job --observation-dir /abs/obs \
        --outcome-dir /abs/outcomes --attestation-dir /abs/attestations \
        --as-of 2026-11-20T12:00:00Z --assumptions-version <id> \
        --entry-slippage-bps <n> --exit-slippage-bps <n> --cost-bps-per-side <n>

Read-only: it opens no database and writes no store or file. Three absolute,
distinct directories and an explicit timezone-aware `--as-of` at or before the
job clock are required (naive or future `as_of` is CONFIG_INVALID at INPUT);
assumptions use the outcome job's own `outcome_assumptions` (no defaults). The
unchanged loaders read every retained EGX observation, every outcome under
exactly these assumptions and every attestation (any damaged or unexpected
entry fails the run at STORES with the loader's reason). Point-in-time
selection RECORDED_AND_EVALUATED_AT_OR_BEFORE_AS_OF_V1 keeps observations with
`recorded_at <= as_of` and outcomes with `evaluated_at <= as_of`; excluded
identities are listed, and an observation whose outcome was evaluated later
stays in the population as NOT_EVALUATED. These are the records' own clocks,
not retention times (RECORD_TIMES_NOT_RETENTION_TIMES). All attestations are
passed to the unchanged `aggregate_egx_checked_decision_outcomes`, which
applies its own check-time rule; an integrity refusal fails at METRICS
(METRICS_REJECTED). Exit 0 REPORTED, 1 FAILED. SYSTEM gap
OUTCOME_METRICS_JOB_NOT_SCHEDULED.

## Checked per-period metrics (`app.decision_outcome_checked_periods`, decision-outcome-checked-period-metrics-v1)

Status: pure library, pending host validation; no caller, job, route or
scheduler (the metrics job does not call it).

`aggregate_egx_checked_decision_outcomes_by_period(population, outcomes,
attestations, *, assumptions, as_of, policy)` composes two unchanged
components: `select_egx_checked_outcomes` (the integrity gate, re-verified
partition and CHECK_AS_OF_AT_OR_BEFORE_AS_OF_V1 rule, extracted from
`aggregate_egx_checked_decision_outcomes` without changing its report) and
`aggregate_decision_outcomes_by_period` over the SAME full population with only
the selected outcomes, under the caller's explicit `EvaluationPeriodPolicy`
(market must be EGX). Inside every period and in UNASSIGNED, an unattested,
invalidly attested, later-attested or missing outcome stays NOT_EVALUATED, so
it cannot hide a loss or shrink a period's sample. Period assignment uses only
`detected_at`. Attestation counts are reported for the whole input only
(`per_period_attestation_counts: NOT_REPORTED`). No cross-period headline,
holdout integrity CALLER_ASSERTED_UNVERIFIED, sample NOT_ASSESSED, no
performance claim. No repository policy instance exists, so gap
EVALUATION_PERIOD_POLICY_NOT_REGISTERED stays open.

## Checked per-event-state metrics (`app.decision_outcome_checked_event_states`, decision-outcome-checked-event-state-metrics-v1)

Status: pure library, pending host validation; no caller, job, route or
scheduler (the metrics job does not call it).

`aggregate_egx_checked_decision_outcomes_by_event_state(population, outcomes,
attestations, *, assumptions, as_of)` composes two unchanged components:
`select_egx_checked_outcomes` (integrity gate, re-verified partition and
CHECK_AS_OF_AT_OR_BEFORE_AS_OF_V1) and `aggregate_decision_outcomes_by_event_state`
(market EGX) over the SAME full population with only the selected outcomes.
Inside every event state an unattested, invalidly attested, later-attested or
missing outcome stays NOT_EVALUATED, so it cannot hide a loss or shrink a
state's sample. State assignment uses only the event-context section retained
in each observation at detection time; attached sections are checked by the
unchanged event-state component (fail closed). All seven states are reported;
attestation counts are whole-input only (`per_state_attestation_counts:
NOT_REPORTED`). The state is inference (`absence_is_proof` false,
`risk_interpretation` NOT_EVALUATED, `trading_effect` NONE); no cross-state
headline, sample NOT_ASSESSED, no performance claim. No gap is closed or added;
operational observations are currently all NOT_ATTACHED.

## Outcome store (`app.decision_outcome_store`, decision-outcome-retention-v1)

Status: library only, pending host validation; no directory provisioned, no
producer, scheduler, route or caller. `retain_decision_outcome(directory,
observation, outcome, *, now=None)` retains one decision-outcome-v1 result for
the retained `(identity, record)` observation it was evaluated for, write-once
and content-addressed (`<sha256>.json`, 0444), reusing the observation and
news-event store directory, lock, write-once and bounded no-follow read
helpers.

- Only terminal outcomes (CLOSED, NO_ENTRY, UNEVALUABLE) are retained;
  PENDING_PATH is OUTCOME_NOT_FINAL. A decision with no retained outcome stays
  NOT_EVALUATED in the aggregate, so withholding an outcome cannot improve a
  segment.
- Before any write: the observation is re-verified against its content
  address and must be recorded by the store clock; the outcome is checked by
  the unchanged `decision_outcome_metrics._check_outcome` against that
  observation (field set, fixed safety labels, current rules, exact
  assumptions in canonical form and of the record's market, binding, evaluated
  by the store clock, registered status/reason, metrics iff CLOSED).
- One outcome per decision and exact assumptions set. A retry differing only
  in `path_complete_through_at`, `evaluated_at` or `bars_supplied` is
  ALREADY_RETAINED with the first record; any other difference (another
  observation, status, exit, price, metric or bar provenance, for example
  after a data backfill or revision) is OUTCOME_CONFLICT. A held loss or a
  missing-data UNEVALUABLE cannot be silently replaced.
- `load_retained_decision_outcomes(directory, *, market, assumptions)` returns
  that market's outcomes under one exact assumptions set ordered by
  observation identity, ready for `aggregate_decision_outcomes` with the
  caller's population; any damaged or unexpected entry, a planted
  non-terminal record or two outcomes for one decision and assumptions fails
  closed (STORE_INVALID).

The reader cannot see the observation and checks structure only; the binding
is checked at retention and again by the aggregate. Bars are not retained, so
numbers are not recomputed; a digest is integrity under a trusted host writer,
not a signature. Results carry `fill_kind` HYPOTHETICAL_FROM_SESSION_BARS,
`execution_evidence` NONE, `performance_claim` NONE, SHADOW, LIVE_MONEY=false,
NOT_AUTHORIZED. SYSTEM declares the component and replaces gap
OUTCOME_RETENTION_NOT_IMPLEMENTED with OUTCOME_STORE_NOT_PROVISIONED.

## Segmented aggregate metrics (`app.decision_outcome_metrics`, decision-outcome-metrics-v1)

Status: pure function, pending host validation. No IO, store, route,
scheduler or caller.

`aggregate_decision_outcomes(population, outcomes, *, market, assumptions,
as_of)` summarises ONE market under ONE exact `OutcomeAssumptions`.
`population` is a tuple of `(identity, record)` pairs (the shape returned by
`load_retained_decision_observations`); `outcomes` is a tuple of
decision-outcome-v1 dicts. Fails closed (ValueError) when:

- a population record does not re-verify against its content address, belongs
  to another market, was recorded after `as_of`, or repeats an identity or
  decision;
- an outcome has the wrong field set, contract/rules version, rules, fixed
  safety labels or assumptions, names no population identity (or repeats
  one), disagrees with its record on decision, market, symbol, instrument,
  strategy id/version, config, detection time or entry trigger, was evaluated
  after `as_of`, or is structurally inconsistent (unregistered status/reason,
  exit/metrics present iff CLOSED, finite decimal R values, MAE ≤ 0 ≤ MFE).

Population records with no outcome are counted as NOT_EVALUATED, so omitting a
losing outcome is visible rather than silent. Population completeness is the
caller's assertion (`CALLER_ASSERTED_UNVERIFIED`); outcome numbers are bound to
their observation but not recomputed (no bars here).

Metrics are reported only per segment (strategy id, strategy version, config
id), with `cross_segment_headline` NOT_COMPUTED. Each segment exposes the
sample size (observations), status counts including NOT_EVALUATED, reason and
exit-reason counts, wins/losses/flat by net R sign, hit rate, mean win/loss net
R, payoff ratio, expectancy net and gross R, total net R, max drawdown in R
(cumulative net R at one R per decision ordered by exit date then identity, no
portfolio constraints), mean MAE/MFE, mean and max holding sessions,
missing-data rate ((UNEVALUABLE + NOT_EVALUATED) / observations) and the
detection-time range. Ratios with a zero denominator are null;
false-positive rate is NOT_DEFINED. Definitions are listed in every result
(`decision-outcome-metric-definitions-v1`). Arithmetic uses 34-digit decimal
precision; output is independent of input order and carries population and
outcome SHA-256 digests. `sample_assessment` is NOT_ASSESSED; fixed
HYPOTHETICAL_FROM_SESSION_BARS, `execution_evidence`/`performance_claim` NONE,
SHADOW, `live_money`/`execution_allowed` false, `promotion` NOT_AUTHORIZED.

## Time-period segmentation (`app.decision_outcome_periods`, decision-outcome-period-metrics-v1)

Status: pure function, pending host validation. No IO, store, route,
scheduler, caller or registered policy.

`aggregate_decision_outcomes_by_period(population, outcomes, *, market,
assumptions, as_of, policy)` takes the same inputs as
`aggregate_decision_outcomes` plus an explicit, versioned
`EvaluationPeriodPolicy` (decision-outcome-period-policy-v1: policy version,
market, aware `registered_at`, one to four `EvaluationPeriod`s). Labels are
DEVELOPMENT, VALIDATION, OUT_OF_SAMPLE and FORWARD_SHADOW, each at most once,
in that chronological order, non-overlapping half-open `[start, end)` aware
intervals. FORWARD_SHADOW must start at or after `registered_at`;
`registered_at` must not be after `as_of`. The policy is revalidated
(model_construct bypass rejected) and its clock is labelled
CALLER_SUPPLIED_UNVERIFIED.

The whole input is first validated once by the unchanged
`aggregate_decision_outcomes`, so every existing fail-closed rule still
applies. Each record is then assigned by its `detected_at` only (never exit or
evaluation time), and each period gets its own unchanged
decision-outcome-metrics-v1 result over its subset, so per-strategy-version
segments stay separate inside each period. Records outside every period are
reported as UNASSIGNED (observation and outcome counts plus identity digest),
not dropped. Each period reports `end_reached_by_as_of` and whether the policy
was registered at or before its start (`REGISTERED_AT_OR_BEFORE_START` /
`REGISTERED_AFTER_START`); holdout integrity is CALLER_ASSERTED_UNVERIFIED.
There is no cross-period headline (`cross_period_headline` NOT_COMPUTED);
`sample_assessment` NOT_ASSESSED, `performance_claim`/`execution_evidence`
NONE, SHADOW, `live_money`/`execution_allowed` false, `promotion`
NOT_AUTHORIZED. Output carries the policy, its SHA-256 and the whole-input
population/outcome digests, and is independent of input order.

## Sample assessment (`app.decision_outcome_sample`, decision-outcome-sample-assessment-v1)

Status: pure function, pending host validation. No IO, store, route,
scheduler, caller or registered policy.

`assess_decision_outcome_sample(population, outcomes, *, market, assumptions,
as_of, policy)` takes the same inputs as `aggregate_decision_outcomes` plus an
explicit, versioned `SampleAssessmentPolicy` (decision-outcome-sample-policy-v1:
policy version, market, aware `registered_at` not after `as_of`,
`min_closed_outcomes` 1..10000, exact Decimal `max_missing_data_rate` in
[0, 1], basis DECLARED_UNVALIDATED, clock CALLER_SUPPLIED_UNVERIFIED). The
policy is revalidated (model_construct bypass rejected).

Metrics come from the unchanged `aggregate_decision_outcomes` (all its
fail-closed rules apply) and are embedded unchanged. Each strategy
id/version/config segment is MEETS_DECLARED_MINIMUM or BELOW_DECLARED_MINIMUM
with reasons CLOSED_OUTCOMES_BELOW_POLICY_MINIMUM and/or
MISSING_DATA_RATE_ABOVE_POLICY_MAXIMUM (thresholds inclusive). Because
NOT_EVALUATED counts as missing, omitting a loss's outcome cannot improve the
assessment. Each segment also reports whether the policy was registered at or
before its first detection, so thresholds fixed after seeing data are visible.
Meeting a declared minimum is not statistical significance
(`statistical_significance` NOT_ASSESSED), not validation
(`empirical_validation` NOT_VALIDATED) and not promotion. No cross-segment
headline; `performance_claim`/`execution_evidence` NONE, SHADOW,
`live_money`/`execution_allowed` false, `promotion` NOT_AUTHORIZED. Output is
independent of input order and carries the policy and its SHA-256.

## Event-state segmentation (`app.decision_outcome_event_states`, decision-outcome-event-state-metrics-v1)

Status: pure function, pending host validation. No IO, store, route,
scheduler or caller.

`aggregate_decision_outcomes_by_event_state(population, outcomes, *, market,
assumptions, as_of)` validates the whole input once with the unchanged
`aggregate_decision_outcomes`, then partitions records by the News/Event state
each record retained when it was recorded: NOT_ATTACHED (no card section) or the
attached trade-card-event-context-v1 section's `event_context_status`
(EVENT_FOUND, NO_EVENT_FOUND, STALE, UNAVAILABLE, AMBIGUOUS, UNKNOWN). Each
state gets its own unchanged decision-outcome-metrics-v1 aggregate. All seven
states are always reported in a fixed order, empty or not, with
`state_counts`; there is no cross-state headline (`cross_state_headline`
NOT_COMPUTED).

Assignment uses only the retained section (bound by the card section to the
detection time), never outcome, exit or evaluation time. Attached sections fail
closed unless they carry the card-section contract version, the
NEWS_EVENT_INFERENCE layer with `observed_market_fact` false and
`trading_effect` NONE, the record's market and canonical instrument, a decision
time equal to the record's detection instant and a registered context status
(the observation reader checks only the section digest, decision binding and
`live_money`). The state is inference: `absence_is_proof` false,
`risk_interpretation` NOT_EVALUATED, `trading_effect` NONE, sample NOT_ASSESSED,
`performance_claim`/`execution_evidence` NONE, SHADOW, `live_money` false,
`promotion` NOT_AUTHORIZED. No state is used to accept, reject or rank anything.
No operational records carry an attached section yet (the card section is not
in the card report and no observation producer exists).

## Data-age segmentation (`app.decision_outcome_data_age`, decision-outcome-data-age-metrics-v1)

Status: pure function, pending host validation. No IO, store, route,
scheduler, caller or registered policy.

`aggregate_decision_outcomes_by_data_age(population, outcomes, *, market,
assumptions, as_of, policy)` validates the whole input once with the unchanged
`aggregate_decision_outcomes`, then partitions records by data age at detection,
`detected_at - data_as_of` in wall-clock seconds, read from the retained record
only (never outcome, exit or evaluation time). The explicit, versioned
`DataAgePolicy` (decision-outcome-data-age-policy-v1: market, aware
`registered_at` not after `as_of` with a CALLER_SUPPLIED_UNVERIFIED clock, 1–8
strictly ascending integer inclusive upper bounds in (0, 366 days], basis
DECLARED_UNVALIDATED; `model_construct` bypass revalidated) defines buckets
`AGE_LE_<b1>S`, `AGE_GT_<b1>S_LE_<b2>S` … `AGE_GT_<bn>S`. Every bucket is always
reported in fixed order with its own unchanged decision-outcome-metrics-v1
aggregate and `bucket_counts`; `cross_bucket_headline` NOT_COMPUTED.

Each record must satisfy `data_as_of <= available_at <= detected_at <
fresh_until` (enforced by `TradeDecision`, not re-checked by the observation
reader) or the call fails closed, so every record was within its
source-declared freshness at detection. Age is not session-aware (`age_basis`
says so): EGX weekends and U.S. holidays add wall-clock age without missed
sessions. `policy_registration` is REGISTERED_AT_OR_BEFORE_FIRST_DETECTION,
REGISTERED_AFTER_FIRST_DETECTION or NO_OBSERVATIONS. `age_interpretation`
NOT_EVALUATED, `trading_effect` NONE, sample NOT_ASSESSED,
`performance_claim`/`execution_evidence` NONE, SHADOW, `live_money` false,
`promotion` NOT_AUTHORIZED. No bucket is used to accept, reject or rank
anything.

## SYSTEM visibility (`app.news_event_status`, news-empirical-feature-status-v1)

Status: code-declared constants, pending host validation. `/api/system` gains
`news_empirical` and the SYSTEM page a "News/Event and empirical validation"
table. The module imports nothing and reads nothing: no news fetch, store
read, observation count, outcome or metric.

Stages are reported separately for News/Event context and empirical
validation: code installed (CODE_INSTALLED), configured
(NOT_CONFIGURED_IN_REPOSITORY), scheduled (NOT_SCHEDULED_IN_REPOSITORY),
producing evidence and evidence current (NOT_OBSERVED), empirically validated
(NOT_APPLICABLE for news, NOT_VALIDATED for empirical) and promoted
(NOT_AUTHORIZED). News status is NEWS_CONTEXT_OFFLINE_ONLY with source
NEWS_SOURCE_NOT_CONFIGURED. The empirical status is
EMPIRICAL_COLLECTION_NOT_ACTIVE: the milestone's EMPIRICAL_COLLECTION_ACTIVE,
EMPIRICAL_SAMPLE_INSUFFICIENT and EMPIRICAL_EVIDENCE_AVAILABLE all imply a
running producer or an observed sample, and none exists. Counts (retained
events, retained observations, evaluated outcomes) are null, rendered UNKNOWN,
never zero; sample assessment NOT_ASSESSED, cross-market headline NOT_COMPUTED,
performance claim NONE, trade-decision event risk UNKNOWN.

Declared components (module and contract/result version) and gaps are checked
against the code by `tests/test_news_event_status.py`: versions must equal the
modules' constants; no other `app/` or `tools/` source references a component
(so no caller, producer, provider, route, card-report binding or scheduler
entry); components read no environment variable; exactly one outcome store
module exists and retains terminal outcomes only (gap
OUTCOME_STORE_NOT_PROVISIONED); the metrics module carries no period segmentation (that lives in
`app.decision_outcome_periods`, with no registered policy instance, gap
EVALUATION_PERIOD_POLICY_NOT_REGISTERED); sample assessment lives in
`app.decision_outcome_sample` with no registered policy instance (gap
SAMPLE_ASSESSMENT_POLICY_NOT_REGISTERED, SYSTEM sample assessment still
NOT_ASSESSED); data-age segmentation lives in `app.decision_outcome_data_age`
with no registered policy instance (gap DATA_AGE_POLICY_NOT_REGISTERED);
`TradeDecision.event_risk` is
`Literal['UNKNOWN']`. Change a declaration only together with the code change
that closes it.

## Not yet done

Host provisioning of the store directories, an ingestion producer for a lawful
free source, wiring the section into the card report/page (which needs a
retained coverage time and a concrete operational policy), a schedule invoking
the observation job, a schedule invoking the outcome job (gap
OUTCOME_JOB_NOT_SCHEDULED), attestation of outcomes held before a checked run
(the job's opt-in `--attestation-dir` attests only outcomes the run itself
retains), a provisioned
attestation directory (gap OUTCOME_ATTESTATION_STORE_NOT_PROVISIONED), a
schedule invoking the read-only checked metrics job (gaps
OUTCOME_METRICS_JOB_NOT_SCHEDULED and OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED),
a provisioned directory for outcome
retention, a registered operational
evaluation-period policy, regime segmentation, a registered
sample-assessment policy, a registered data-age policy and session-aware data
age (Phases I–L). Tests use artificial records only.
