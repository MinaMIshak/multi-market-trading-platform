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

## Not yet done

Retained storage and an ingestion producer for a lawful free source, the Trade
Card event-context projection (EVENT_FOUND / NO_EVENT_FOUND / STALE /
UNAVAILABLE / AMBIGUOUS / UNKNOWN), freshness policy, SYSTEM state and empirical
validation (Phases E–L). Tests use artificial records only.
