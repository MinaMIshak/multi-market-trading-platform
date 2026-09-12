# R1.2 retrospective PIT daily derivation

R1.2 is a research-only engineering boundary for deterministic retrospective
EGX point-in-time daily derivation.

It does not acquire real market data, certify source authenticity, validate a
strategy, prove profitability, establish paper readiness, or establish
live-money readiness. Fixture tests prove software behavior only.

## R1.1 prerequisite and availability

Every historical fact consumed by R1.2 carries a canonical
`HistoricalEvidencePackage`.

R1.1 is the only authority for deciding whether the exact consumed historical
fact was safely available by `decision_at`.

Row dates, action effective dates, `published_at`, source snapshot dates, and
current repositories are not substitutes for R1.1 availability proof.

Retrospective local receipt and review may occur after the historical decision,
but all dependencies required by a build must exist by `research_built_at`.

A candidate whose R1.1 availability is later than `decision_at` is excluded.
A bounded availability interval is usable only when its inclusive end is at or
before `decision_at`. Excluded future facts cannot repair missing historical
coverage or change the derived historical result.

Malformed or corrupted candidate contracts still fail canonical validation even
when their availability would otherwise place them in the future.

## Exact dated calendar, universe, and identity

R1.2 requires one admitted `HistoricalSessionRecord` for every calendar date
from `start_date` through `decision_date`, inclusive.

No weekday, weekend, holiday, closure, or exchange-hours inference occurs.
`NON_SESSION` requires `NOT_APPLICABLE` and cannot contain a daily bar.
`SUSPENDED` is explicit evidence, never an inference from a missing bar.
`UNSUPPORTED` fails closed. No session hours are synthesized.

Every trading session also requires exactly one complete, exact-date EGX
`UniverseEvidence` snapshot and exactly one admitted `HistoricalIdentityMapping`
for the target stable instrument.

There is no current-universe lookup, forward fill, latest-wins reconstruction,
membership interval reconstruction, or current-security-master fallback.

The same stable `instrument_id` may have different symbols on different dates
only when those exact dated mappings are independently evidenced. Ambiguous
same-date provider-symbol mappings fail closed.

These rules are designed to prevent look-ahead and survivorship leakage at the
R1.2 boundary.

## Daily observations

R1.2 reuses `CanonicalDailyBar`.

Only `VALID_EXECUTABLE` rows may be consumed. Stable instrument identity,
canonical symbol, provider symbol, and source provider must match the exact dated
identity mapping.

The historical package must bind the exact source SHA256 and cover the complete
raw daily record fields used by the research artifact, including `quality_flags`
and `provider_adjusted_close_reference`.

Raw OHLCV is never modified.

`provider_adjusted_close_reference` is audit/reference data only. It is never
used as an executable price and never replaces raw or indicator prices.

## Corporate actions and indicator series

Action evidence must exactly cover `start_date` through `decision_date` for the
target instrument.

R1.2 v1 supports only explicit `SPLIT` events. Any other relevant corporate
action fails closed instead of inventing economic semantics.

The transformation version is `split-only-v1-decimal34`.

Arithmetic uses a local Decimal context with precision 34. For each raw bar,
every split satisfying:

`bar.market_date < event.effective_date`

contributes:

```text
price_factor  *= old_shares / new_shares
volume_factor *= new_shares / old_shares
```

Indicator OHLC is raw OHLC multiplied by `price_factor`; indicator volume is raw
volume multiplied by `volume_factor`.

Events are ordered by `(effective_date, event_id)`. Raw
`CanonicalDailyBar` objects remain unchanged. The provider adjusted-close
reference is never used for this transformation.

`ResearchIndicatorBar` is an indicator/research representation only. It is not
intraday execution evidence and does not synthesize fills, gaps, stops, or
broker economics.

## Deterministic derivation identity

`ResearchPITDaily.derivation_id` is a SHA256 digest over stable canonical
serialization of only semantically used admitted historical facts.

It binds contract/transformation versions, target instrument, requested dates,
`decision_at`, admitted raw rows, exact dated session/universe/identity facts,
exact action coverage/events, R1.1 evidence identities actually used, and the
resulting indicator rows.

Excluded future facts do not enter the identity.

`research_built_at` is an audit/admission clock and is intentionally excluded
from the semantic identity. Rebuilding the same admitted historical truth later
therefore preserves the same `derivation_id`.

## Operational isolation

R1.2 does not call or refactor `PointInTimeDailyRepository` and does not produce
`PointInTimeDailyDataset`.

It performs no network/provider/API access, data acquisition, database or
storage write, schema migration, production action, Candidate/TradePlan/
RiskDecision construction, paper execution, M7 performance construction, or M8
validation construction.

R1.2 is a pure retrospective research derivation boundary.

## Limitations

No real historical EGX data has been acquired by R1.2.

Passing fixture tests does not prove real source availability, authenticity,
historical universe completeness, identity history, session history, corporate
action completeness, absence of upstream source bias, strategy profitability,
or live readiness.

R1.3 is not implemented here.
