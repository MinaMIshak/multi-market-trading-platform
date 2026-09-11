# US7B Historical US Intraday Truth

US7B admits retrospective raw, unadjusted US intraday market observations
for later execution research.

It is an evidence/admission layer only.

US7B does not create shared M6 `IntradayBar` objects, paper fills,
positions, trade plans, risk decisions, performance observations, broker
orders, or live execution artifacts.

## Boundary

The flow is:

reviewed historical evidence
→ stable exact-dated US listing identity
→ explicit US session truth
→ raw intraday artifact segment(s)
→ contiguous admitted intraday session/window

US7B stops at admitted historical truth.

Conversion to the shared M6 execution contract belongs to US7C.

## Evidence cutoff versus strategy decision

`evidence_cutoff_at` is deliberately not a strategy decision timestamp.

Historical execution observations may occur after the strategy signal or
planning decision.

R1.1 historical evidence is therefore re-admitted against an explicit
execution-truth cutoff.

Every individual bar retains its own `available_at_utc`.

A bar whose availability is later than `evidence_cutoff_at` is rejected.

This prevents future observations from entering an earlier historical
execution replay.

## Stable identity

Ticker text is not security identity.

Every admitted bar and segment is bound to:

- stable `instrument_id`;
- exact historical `canonical_symbol`;
- calendar/listing MIC;
- exact market date;
- provider instrument key and provider symbol;
- raw source SHA-256;
- provenance identity.

An exact-dated listing identity must exist for the market date.

No current ticker projection, latest-wins mapping, or symbol backfill is
used.

## Explicit session truth

US7B reuses admitted US2 session history.

No regular-hours, weekend, holiday, DST, or early-close inference is
performed.

An explicitly `CLOSED` session cannot contain intraday truth.

`REGULAR` and `EARLY_CLOSE` sessions use the exact admitted
`opens_at_utc` and `closes_at_utc`.

## Supported granularity

US7B currently admits only:

- `M1`
- `M5`

Daily observations are not accepted as intraday observations.

Every interval width must exactly match its declared granularity.

## Raw price basis

US7B uses:

`RAW_UNADJUSTED`

No split, dividend, total-return, or provider-adjusted price transform is
performed inside this admission layer.

Corporate-action transformation remains outside US7B.

## Artifact segments

One `HistoricalUSIntradaySegmentFact` represents one reviewed raw
coverage artifact.

A fact contains:

- one explicit coverage segment;
- the raw bars belonging to that segment;
- one `HistoricalEvidencePackage`.

The segment provider and SHA-256 must match the historical raw receipt.

The reviewed evidence must cover all required US intraday fields.

Multiple raw artifacts may compose one admitted session/window, but they
must already be chronologically ordered.

US7B does not sort, repair, interpolate, or fill gaps.

Adjacent segments must meet exactly and true session sequence numbers
must continue without gaps.

## Completeness

Inside each segment:

- first bar begins at the declared segment start;
- last bar ends at the declared segment end;
- each interval has the declared width;
- timestamps are contiguous;
- session sequence numbers are consecutive;
- declared sequence span equals observed bar count.

Missing observations are never manufactured.

Raw source-row numbers must be strictly increasing inside one artifact.

They do not need to be consecutive because the original artifact may
contain other records.

## Coverage modes

US7B defines two explicit coverage modes.

### FULL_SESSION

Coverage must begin exactly at the admitted US2 session open and end
exactly at the admitted US2 session close.

Sequence must begin at 1.

Final sequence and total bar count must match the complete explicit
session grid for the selected granularity.

### BOUNDED_WINDOW

A bounded window may cover only part of an explicit open session.

It must remain aligned to the session-origin granularity grid.

Sequence numbers remain the true session sequence numbers.

A later window cannot be renumbered to sequence 1.

This prevents truncated historical observations from masquerading as
opening-origin execution history.

## Availability

`interval_end_utc <= available_at_utc` is required.

Delayed availability is preserved rather than rewritten.

Historical evidence availability must be safe by
`evidence_cutoff_at`.

Receipt, attachment, review, and availability clocks continue to use the
existing R1.1 evidence contract.

## Determinism

Admitted US7B identity includes semantic evidence and admitted facts but
excludes the local `research_built_at` clock.

Rebuilding identical semantic historical truth later therefore does not
change its identity merely because the local research build occurred
later.

Evidence-package identities remain part of historical fact identities.

## M6 boundary

US7B intentionally does not materialize `app.data.intraday.IntradayBar`.

That conversion belongs to US7C.

The shared M6 contract requires opening-origin, chronological execution
streams and rejects truncated suffixes that could manufacture a false
`NO_FILL`.

US7C must therefore establish explicit execution eligibility from
admitted US7B truth before invoking M6.

A US7B admission alone is not executable and is not paper-trading
authorization.

## Multi-session Swing plans

US7A plans may remain valid across more than one market session.

The current shared M6 input is single-session.

US7B does not silently shorten a plan or merge sessions to hide that
difference.

US7C must explicitly solve the multi-session replay boundary before a
multi-session Swing plan can produce historical execution evidence.

## Non-goals

US7B performs no:

- provider network acquisition;
- paid market-data calls;
- database writes;
- production changes;
- scheduler changes;
- daily-to-intraday synthesis;
- missing-bar interpolation;
- session-time inference;
- corporate-action price adjustment;
- M5 risk admission;
- M6 paper simulation;
- broker integration;
- live-money execution.
