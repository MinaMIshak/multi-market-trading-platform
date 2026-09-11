# US3 Historical Daily Bar Admission

US3 defines the retrospective research boundary for raw United States
daily OHLCV observations.

It does not modify the operational EGX daily-data pipeline or reuse the
EGX-specific `egx-daily-semantic-v1` contract as a US market contract.

## Raw means raw

US3 admits `RAW_UNADJUSTED` OHLCV only.

Provider adjusted close, when supplied, is retained exclusively as an audit
reference. It is never:

- an execution price;
- substituted for raw close;
- used to derive OHLC;
- used to apply split or dividend transformations.

Corporate-action transforms belong to later PIT assembly after explicit
corporate-action evidence is admitted.

## Stable security identity

Ticker is not identity.

Every bar contains a stable `instrument_id` and must match an exact-dated
US listing identity for its market date.

The bar's canonical symbol and listing MIC must match that exact-dated
identity.

This prevents current ticker projection and ticker-reuse leakage.

Provider-specific symbol/key fields remain source provenance and may come
from a different reviewed provider from the listing-identity evidence.

## Explicit market sessions

Every admitted bar date must resolve to explicit US2 session truth.

A bar is rejected when the explicit session is `CLOSED`.

Bar presence does not establish that a market was open, and bar absence does
not establish that a market was closed.

## Sparse observation semantics

A US3 admitted history is a sparse set of positively evidenced observations.

Coverage bounds do not assert that every open session contains a bar.

Missing bars are not:

- forward filled;
- back filled;
- interpreted as zero volume;
- interpreted as a closed exchange;
- silently accepted as complete instrument history.

Downstream PIT/universe logic must determine whether a missing observation is
acceptable for the exact instrument and research question.

## Historical evidence

Every daily-bar fact is re-admitted through R1.1 historical evidence.

Evidence covers the bar semantics, stable instrument mapping, raw price basis,
source provenance, and raw receipt hash.

The review remains a trusted attestation rather than cryptographic proof that
the external data source itself is correct.

## Point-in-time dependencies

US3 requires canonical admitted:

- US1 exact-dated listing identity;
- US2 explicit session history.

Dependency histories are re-admitted before use.

All three research boundaries use the same `decision_at` horizon.

## No corporate-action inference

US3 performs no split, dividend, merger, spinoff, symbol-change, or delisting
adjustment.

Price jumps do not imply corporate actions.

Future corporate actions cannot alter US3 historical prices because US3 does
not consume corporate-action transforms at all.

## Determinism

Facts are ordered by market date.

Semantic history identity includes:

- instrument;
- MIC;
- coverage bounds;
- decision time;
- exact listing-history identity;
- exact session-history identity;
- ordered daily-bar fact identities.

`research_built_at` is deliberately excluded from semantic identity.

## Non-goals

US3 performs no:

- network acquisition;
- provider API calls;
- database writes;
- operational scheduler changes;
- broker interaction;
- live execution;
- adjusted price generation.

No real US market data is acquired or validated by US3.
