# US1 — Historical US Identity Admission

Status: retrospective research identity foundation only.

US1 binds exact-dated `USListingIdentity` facts to R1.1
`HistoricalEvidencePackage` objects and admits those facts only when their
historical availability was safely known by the requested decision time.

It performs no acquisition and makes no current-market identity projection.

## Boundary

The US1 boundary is:

    USListingIdentity
            +
    HistoricalEvidencePackage
            |
            v
    HistoricalUSListingFact
            |
            v
    admit_us_listing_history(...)
            |
            v
    AdmittedUSListingHistory

US1 does not modify the operational EGX security master, EGX reference
repository, point-in-time repository, database, providers, or strategies.

## Exact-date semantics

`USListingIdentity.effective_date` is an exact dated fact.

US1 deliberately provides no:

- forward fill;
- backward fill;
- latest-wins mapping;
- current-ticker projection into history;
- ticker-continuity inference;
- company-name matching;
- price-series identity inference.

`resolve_us_listing_on_date()` requires an exact fact for the requested
instrument and market date.

A symbol may change across different exact dates while retaining the same
stable `instrument_id`.

A ticker may be reused by a different instrument on a different date.

The same `(date, listing_mic, canonical_symbol)` may not ambiguously identify
two instruments.

## Evidence admission

Every submitted fact is re-admitted through R1.1
`require_historical_evidence()`.

Admission requires, among other R1.1 rules:

- reviewed immutable source receipt/evidence binding;
- approved source review;
- local receipt no later than `research_built_at`;
- attachment receipt no later than `research_built_at`;
- review no later than `research_built_at`;
- latest safe historical availability no later than `decision_at`.

US1 additionally requires the reviewed `covered_fields` claim to include all
fields needed to interpret the US listing identity and requires the listing
`source_provider` to equal the historical raw receipt provider.

`covered_scope` and `covered_fields` are reviewed attestations. They are not
cryptographic proof that arbitrary semantic values occur in source bytes.
R1.1 source review remains a trusted attestation.

## UTC canonicalization

US1 requires exact `datetime.timezone.utc` for its public decision/build clocks
and for all R1.1 clocks admitted through the US boundary.

This is stricter than accepting an arbitrary timezone object whose numeric UTC
offset merely equals zero.

## Future facts

US1 is fail-closed.

A submitted evidence package whose historical availability is after the
decision time is rejected rather than silently included.

An identity fact whose effective market date is later than the US-local
decision date is also rejected, even if that future change had been announced
earlier.

US1 therefore cannot use a future ticker mapping to repair a missing historical
identity fact.

## Determinism

Admitted facts are sorted canonically.

`AdmittedUSListingHistory.identity` binds:

- schema version;
- decision timestamp;
- semantic/evidence identity of every admitted fact.

`research_built_at` is intentionally excluded from the semantic identity so
that rebuilding the identical admitted historical subset later does not create
a different semantic history identity.

## Non-goals

US1 contains no:

- provider calls;
- web requests;
- purchased data;
- database writes;
- schema migration;
- operational calendar changes;
- universe derivation;
- OHLCV ingestion;
- corporate-action transformation;
- trading strategy;
- broker execution;
- profit or performance claim.

Real US market data has not been acquired or validated by this milestone.
