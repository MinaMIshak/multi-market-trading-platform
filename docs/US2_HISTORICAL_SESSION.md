# US2 Historical Session Admission

US2 defines the retrospective research boundary for explicit United States
market-session truth.

It is a research-only contract. It does not modify or reuse the operational
EGX calendar, scheduler, PIT pipeline, database, provider adapters, broker
integration, or production runtime.

## Pipeline

The admission path is:

`USSessionRecord + HistoricalEvidencePackage`
→ `HistoricalUSSessionFact`
→ `admit_us_session_history`
→ `AdmittedUSSessionHistory`
→ `resolve_us_session_on_date`

Every submitted historical fact is re-admitted through the R1.1 historical
evidence boundary.

## Explicit calendar truth

US2 never infers a session from:

- weekday/weekend rules;
- the absence or presence of OHLCV bars;
- common exchange holiday knowledge;
- regular-hours assumptions;
- DST assumptions;
- nearby sessions;
- current calendar state.

Every calendar date in the inclusive requested coverage interval must have
exactly one explicit session fact.

Closed dates therefore require explicit `CLOSED` session facts.

## MIC scope

Each admitted history belongs to exactly one canonical four-character
`calendar_mic`.

For example, an XNAS history and an XNYS history are independent evidence
sets even when they cover the same dates.

A fact for another MIC cannot enter the admitted history.

## Point-in-time horizon

US2 represents retrospective effective session history only.

`coverage_end` cannot exceed the US-local date corresponding to
`decision_at`, using `America/New_York`.

A future-effective session fact cannot enter the admitted retrospective
history even if a future exchange schedule happened to be announced early.

Forward-looking exchange schedules are a separate future contract and are
not represented by US2.

## Historical evidence

Evidence must cover at least:

- `market_date`
- `calendar_mic`
- `timezone_name`
- `state`
- `opens_at_utc`
- `closes_at_utc`

Additional reviewed fields are permitted.

Every package is revalidated with `require_historical_evidence`.

The historical source review and its `covered_scope` / `covered_fields`
remain trusted attestations. They are not cryptographic proof that the
external source itself was authentic or correct.

## Canonical UTC

US2 strengthens the historical evidence boundary by requiring the exact
`datetime.timezone.utc` object for:

- `decision_at`;
- `research_built_at`;
- raw receipt clocks;
- attachment receipt clocks;
- review clocks;
- exact or bounded historical availability clocks.

A custom zero-offset timezone is not accepted as canonical UTC.

## Determinism

Facts are deterministically ordered by calendar date.

The semantic history identity includes:

- schema version;
- MIC;
- coverage start/end;
- decision time;
- ordered admitted fact identities.

`research_built_at` is deliberately excluded from semantic identity.
Rebuilding the same admitted historical truth later therefore does not
change its semantic identity.

## Resolution

`resolve_us_session_on_date` performs exact-date resolution only.

It does not:

- forward fill;
- back fill;
- choose a nearest session;
- choose the previous trading day;
- choose the next trading day;
- infer a closed day.

An out-of-coverage date fails closed.

## Non-goals

US2 performs no network acquisition, provider calls, database writes,
scheduler operations, broker operations, or production changes.

No real US market data has been acquired or validated by this milestone.
