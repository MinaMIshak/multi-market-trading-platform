# News/Event Context — evidence contract (pending host validation)

Status: pure contract, point-in-time read rule, projection, codec, write-once
store library and a separate card-section function. No provider, route,
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

## Not yet done

Host provisioning of the store directories, an ingestion producer for a lawful
free source, wiring the section into the card report/page (which needs a
retained coverage time and a concrete operational policy), SYSTEM state, a
producer calling the observation store, a host source of retained session bars
for the evaluator, outcome retention, time-period/regime/event-state
segmentation, sample-sufficiency policy and out-of-sample period labelling
(Phases I–L). Tests use artificial records only.
