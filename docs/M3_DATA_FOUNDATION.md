# M3 data foundation

M3 adds an offline point-in-time consumption boundary. It does not fetch data,
register jobs, enable paper refresh, or implement an engine. Existing provider
adapters, current security-master lookup, raw/canonical storage, and the calendar
and quota gates remain in place. Existing canonical artifacts describe structural
quality; they alone do not establish historical eligibility or source authority.
Future engine consumers must use `PointInTimeDailyRepository`, rather than resolve
historical inputs against `SecurityMasterRepository`'s current snapshot.

## Evidence contracts

`ReferenceRepository.ingest` accepts local bytes, a provider identity, a source URI,
and an explicit contract. Original bytes go through `ImmutableRawStore` and
`DataIngestionRepository`; a content-addressed filename preserves conflicting
versions. Invalid input remains raw, is marked REJECTED, and has a durable DQ issue.
Valid input is structurally VALIDATED and cataloged in `reference_artifacts`.
This is not automatic source promotion.

These contracts are internal, curated interchange formats, **not claims about any
provider's API fields or completeness guarantees**. No real evidence or provider
adapter is supplied by this milestone. Inputs must be prepared by a trusted local
importer/reviewer with documented source semantics:

- `egx-universe-v1`: EGX, an exact effective date, publication timestamp, explicit
  completeness, and stable instrument UUID / symbol / eligible boolean entries.
  Duplicate IDs or symbols are rejected. Empty membership is explicit evidence of
  no eligible members, not a reason to fall back to today's master.
- `egx-actions-v1`: stable instrument UUID, inclusive coverage start/end, publication
  timestamp, explicit completeness, and events with unique IDs/effective dates.
  An empty event list is usable only because completeness and coverage are explicit.
  Splits require **new shares / old shares**. No ratio is inferred from prices.
  Same-date multiple actions are ambiguous in v1 and rejected.
- `egx-source-review-v1`: approved review tied to a specific persisted ingestion UUID,
  SHA256, provider, and semantic contract. It requires reviewer identity, review time,
  evidence URI, and methodology. The review itself is immutable raw evidence with
  provenance. This is a trusted local attestation interface, not cryptographic
  authentication or an automated verifier of a reviewer's claims. Mere provider
  names (including EGX/EGID) never bypass review; secondary sources need the same
  explicit validation before consumption. URIs are stored, never fetched.

All fields are defined by the typed models in `app/data/reference.py`; undeclared
fields, duplicate JSON keys, partial reference documents, naive timestamps, false
completeness, and publication after local receipt are rejected. Review authenticity
and adequacy require actual source evidence before any operational import; synthetic
test attestations establish no authority over real EGX data.

## Point-in-time consumption

`PointInTimeDailyRepository.load(raw_path=..., universe_date=...,
expected_market_date=..., as_of=...)` uses persisted daily ingestion metadata and
`DailyCanonicalPipeline` to validate the original bytes. No provider is invoked.
The caller supplies the expected completed market date from verified calendar
truth; this layer never guesses holidays or manufactures a session.

Receipt, reference validation completion, publication, and review availability
must be at or before the timezone-aware knowledge cutoff. Evidence received today
cannot satisfy a historical cutoff, even if its effective dates are historical.
The date being reconstructed and the knowledge cutoff are separate explicit inputs.
A later cutoff can reconstruct what is known later; it must never be substituted
for an earlier decision's cutoff in backtests.

The selected universe and every historical observation date require exactly one
reviewed, complete dated universe. Each must explicitly establish instrument identity,
symbol, and eligibility. No forward-fill, interval extrapolation, current-master
fallback, delisted-name removal, or ticker-change inference occurs. Histories with
missing reference dates fail closed, even when daily prices exist.

Daily observations require matching provenance, complete canonical OHLCV, unique
dates, requested date bounds, no dates after the source snapshot, and an exact latest
expected market date. Quarantined rows reject the dataset instead of silently being
filtered. Different bytes for the same provider/symbol/snapshot block consumption of
both versions once known, including a rejected competing version. Identical replay
preserves identity. Competing dated reference sources are ambiguous rather than
resolved by source ordering or newest-wins selection. Corrections need an explicit
future adjudication contract; none is guessed here.

Corporate-action coverage must span the earliest observation through the universe
date. A unique reviewed document must cover the whole interval; overlapping sources
block rather than being merged. Known splits effective by the universe date produce
separate indicator-only observations: prices multiply by old/new and volume by
new/old, only for bars strictly before the event date. Precision is fixed at 34
Decimal digits. Original prices, provider-adjusted-close reference, and raw bytes
remain unchanged. Provider-adjusted close is never used to calculate these values.
Future-effective events do not adjust earlier observations. Dividend, rights,
capital changes, symbol changes, delisting and other events are retained as evidence
but block this consumer because transformation semantics are not implemented.

A successful read has VALIDATED DQ status and records a deterministic audit event
with source/review IDs, raw hash, cutoff, original observations, transformation
version, event IDs, factors, and derived values. Identical replay after restart
returns the same audit identity without duplicate events. Rejections persist
context-specific, idempotent DQ issues and raise; no executable dataset is returned.

## Persistence and limits

Schema 9 adds only the reference catalog, its ingestion foreign key, and update/delete
rejection triggers. No current-universe rows are backfilled into history. The existing
explicit upgrade gate and newer-schema refusal remain. All migrations exercised for
M3 use isolated synthetic test databases. No production migration is performed.

This is deliberately conservative: per-date reference evidence may be costly to
curate; multiple sources cannot yet be adjudicated; non-split action transformations
are unsupported. Real provider validation and real historical reference coverage
remain operational prerequisites. M3 does not assert that those datasets exist.
M4 is not started.
