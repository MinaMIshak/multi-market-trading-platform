# News/Event Context — evidence contract (pending host validation)

Status: pure contract and point-in-time read rule only. No provider, IO, storage,
route, scheduler, Trade Card field or caller. News is evidence enrichment, never a
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

## Not yet done

Host provisioning of the store directory, an ingestion
producer for a lawful free source, attaching
the context to the Trade Card display (separately from observed market facts),
a concrete operational freshness policy, SYSTEM state and empirical validation
(Phases G–L). Tests use artificial records only.
