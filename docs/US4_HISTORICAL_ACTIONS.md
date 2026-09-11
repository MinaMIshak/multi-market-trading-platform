# US4 Historical Corporate Actions Admission

US4 defines the retrospective research boundary for complete bounded United
States corporate-action truth.

It is an evidence/admission layer only. It does not transform prices or
volumes.

## Stable instrument identity

Corporate-action coverage belongs to a stable platform `instrument_id`.

A ticker is not used as the action-history identity.

US4 deliberately does not require an exact-dated US1 listing fact for every
corporate-action effective date. Corporate-action dates, listing-identity
effective dates, and trading-session dates are distinct semantic concepts.

Cross-contract identity coherence, including symbol-change timeline checks,
belongs to downstream PIT assembly.

## Complete bounded coverage

Each `USCorporateActionCoverage` is explicitly complete for an inclusive
bounded interval.

An empty action tuple is therefore meaningful evidence:

`complete=True, actions=()`

means that the reviewed source asserts no corporate actions occurred in that
covered interval.

It does not mean that action data is unavailable.

When multiple complete coverage facts are admitted, they must form an exact,
non-overlapping, gap-free partition of the requested historical interval.

## Supported event facts

The US0 contracts represent:

- SPLIT
- CASH_DIVIDEND
- STOCK_DIVIDEND
- MERGER
- SPINOFF
- SYMBOL_CHANGE
- DELISTING
- RIGHTS
- OTHER

US0 already requires explicit action-specific terms where supported:

- splits require explicit `new_shares` and `old_shares`;
- symbol changes require explicit distinct old/new symbols;
- cash dividends require an explicit USD cash amount.

US4 preserves these facts exactly.

## No market-data inference

US4 never infers corporate actions from:

- price gaps;
- volume jumps;
- adjusted-close differences;
- missing bars;
- ticker discontinuities;
- session-calendar behavior.

An action must be explicitly present in reviewed corporate-action evidence.

## No session dependency

A corporate-action effective date is not inferred to be a market session.

US4 therefore does not reject an explicit source action merely because its
effective date is a weekend, holiday, or otherwise non-trading date.

Session/action coherence required by a specific downstream trading transform
belongs to PIT assembly.

## No transformation

US4 performs no:

- split price adjustment;
- split volume adjustment;
- dividend adjustment;
- total-return adjustment;
- dividend reinvestment;
- merger conversion;
- spinoff valuation;
- rights valuation;
- delisting-price inference.

Transforms are downstream operations with their own explicit semantics.

## Point-in-time horizon

Requested coverage cannot extend beyond the US-local date corresponding to
`decision_at`.

Every evidence package is re-admitted through R1.1, so historical
availability must already have been safely knowable by the decision horizon.

Receipt, attachment, and review clocks must not exceed the research build.

US4 additionally requires exact `datetime.timezone.utc` clocks at the US
research boundary.

## Determinism

Coverage facts are deterministically ordered by their coverage bounds.

Actions inside each coverage object are deterministically ordered by:

1. effective date;
2. event ID;
3. action type.

Semantic history identity includes:

- stable instrument ID;
- requested coverage bounds;
- decision time;
- ordered historical coverage fact identities.

`research_built_at` is deliberately excluded from semantic identity.

## Exact-date resolution

`resolve_us_corporate_actions_on_date` returns only events whose explicit
effective date equals the requested covered date.

For a covered date with no events it returns an empty tuple. This is complete
negative evidence rather than a missing-data condition.

Dates outside admitted coverage fail closed.

## Trusted attestation boundary

Historical review coverage remains a trusted attestation. It is not
cryptographic proof that the external corporate-action source itself was
correct or authentic.

## Non-goals

US4 performs no:

- provider API acquisition;
- network calls;
- database writes;
- production changes;
- operational scheduler integration;
- broker interaction;
- live trading.

No real US corporate-action data is acquired or validated by this milestone.
