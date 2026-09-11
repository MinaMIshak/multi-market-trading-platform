# US5A Historical PIT Universe Admission

US5A defines the retrospective evidence boundary for complete exact-date
United States research-universe snapshots.

Its primary purpose is to prevent survivorship bias and current-membership
projection from entering historical research.

## Exact-date semantics

Each `USUniverseSnapshot` is complete for exactly one `effective_date`.

US5A deliberately provides no:

- latest-wins rule;
- forward fill;
- backward fill;
- membership interval inference;
- current-members projection.

A missing snapshot remains missing.

## Complete negative evidence

An exact-date snapshot with:

`complete=True, members=()`

is valid complete negative evidence: the reviewed source asserts that the
universe contained no members for that exact date.

That is different from having no snapshot for the date.

## Survivorship protection

Historical research must preserve securities that were valid members at the
historical date even if they later:

- delisted;
- merged;
- changed ticker;
- failed;
- disappeared from the current provider universe.

Likewise, a security that joined later cannot be projected backward into an
earlier universe.

## Stable identity and ticker reuse

Universe members use stable `instrument_id` values.

Symbols and listing MICs are exact-date attributes.

Across different exact dates:

- one instrument may legitimately change symbol;
- one symbol may legitimately be reused by another instrument.

US5A does not infer continuity between those facts.

## Independent evidence boundary

US5A does not require US1 identity, US2 session, US3 daily-bar, or US4
corporate-action histories during universe admission.

Those sources remain independent historical-evidence boundaries.

Cross-contract consistency is enforced downstream during US5B retrospective
PIT assembly.

This avoids silently changing universe truth because another independent
source is absent.

## Historical evidence

Every universe fact is re-admitted through the R1.1 historical-evidence
boundary.

Required reviewed universe semantics are:

- effective date;
- completeness;
- members.

The source review remains a trusted attestation rather than cryptographic
proof that the external source itself was correct.

## Point-in-time horizon

A future-effective universe snapshot cannot enter admitted retrospective
history.

Historical evidence availability must be safely known by `decision_at`.

Receipt, attachment, and review clocks must not exceed the research build.

US5A also requires exact `datetime.timezone.utc` clocks at the US research
boundary.

## Determinism

Facts are deterministically ordered by effective date.

Semantic history identity includes:

- schema version;
- decision time;
- ordered exact-date universe fact identities.

`research_built_at` is deliberately excluded from semantic identity.

## Exact resolution

`resolve_us_universe_on_date` resolves only an exact-dated admitted snapshot.

It never substitutes a previous or later universe snapshot.

## Non-goals

US5A performs no:

- network/provider acquisition;
- database writes;
- scheduler changes;
- price transformation;
- security filtering based on current status;
- broker operations;
- live trading.

No real US universe data is acquired or validated by this milestone.
