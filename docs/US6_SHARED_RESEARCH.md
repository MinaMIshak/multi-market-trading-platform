# US6 Shared Daily Research Boundary

US6 connects the US retrospective point-in-time daily dataset from US5B
to the existing Swing strategy mathematics without duplicating the EGX
strategy implementation.

US6 is research-only. It does not create executable orders, trade plans,
risk admissions, fills, paper executions, performance observations, or
research evidence.

## Architecture

The daily Swing calculation is split into two layers:

1. `evaluate_swing_series`
   - pure shared strategy mathematics;
   - consumes already-admitted close/high series;
   - computes the existing EMA/trend/breakout gate;
   - performs no repository, market-data, execution, network, or database I/O.

2. Market-specific adapters
   - EGX `SwingEngine` retains the existing M3 `PointInTimeDailyRepository`
     admission path and legacy `Candidate` output;
   - US `evaluate_us_swing` consumes one US5B
     `USRetrospectivePITDailyDataset`.

The extraction deliberately preserves the existing EGX Swing behavior.

## US price semantics

US Swing indicators consume US5B `split_adjusted` values.

These values exist only for indicator continuity across explicit stock
splits.

A WATCH result's planning price reference remains the raw historical
close from the matching US5B daily bar.

Therefore:

- split-adjusted prices are indicator inputs;
- raw prices remain planning/reference prices;
- provider adjusted-close fields are not used;
- cash dividends are not transformed into total-return prices.

The declared price basis is:

`RAW_CLOSE_REFERENCE_SPLIT_ADJUSTED_INDICATORS`

## Stable US identity

The legacy strategy `Candidate` is symbol-oriented and does not carry
the stable US instrument identity required to protect research across
ticker changes and ticker reuse.

US6 therefore uses `USSwingResearchResult`, which preserves:

- stable `instrument_id`;
- exact historical `canonical_symbol`;
- listing MIC;
- signal date;
- exact decision timestamp;
- US5B semantic dataset identity;
- Swing configuration identity.

US6 does not collapse stable US identity into ticker identity.

## Information-time boundary

US5B supports retrospective assembly where the research decision timestamp
may be later than the requested historical coverage interval.

US6 is stricter.

For one historical daily Swing decision, the US-local date represented by
`decision_at` must equal `coverage_end`.

The dataset must also contain an eligible admitted observation exactly on
`coverage_end`.

This prevents a later retrospective dataset from being silently reused as
if it were an earlier historical trading decision.

After the signal-date observation is established, the adapter separately
checks that sufficient prior history exists for the declared Swing
configuration.

## Validation status

Every US6 result remains:

- `validation_status = UNVALIDATED`
- `execution_allowed = False`

A WATCH state means only that the declared mathematical Swing gate passed
on the admitted PIT series.

It is not evidence of alpha, profitability, executability, paper readiness,
or live-money readiness.

## M6 / M7 / M8 boundary

US6 does not synthesize M6 paper executions.

Existing M6 paper simulation requires canonical intraday execution bars,
a canonical trade plan, and canonical risk admission.

US5B currently establishes daily historical PIT truth. Daily OHLCV must not
be converted into invented intraday fills merely to feed M6.

M7 and M8 remain reusable downstream once genuine canonical execution
observations exist:

- M7 owns performance economics;
- M8 owns development partitioning, walk-forward/OOS evaluation, and frozen
  holdout evidence.

US execution-evidence integration is deferred to US7.

## Integrity limitation

`USRetrospectivePITDailyDataset` is an in-memory research dataclass whose
semantic identity is deterministically derived from its contents.

US6 validates its required structure, identity fields, timing, price basis,
row alignment, and data-quality state, but the dataset object itself is not
a cryptographic proof that only the US5B builder could have constructed it.

Historical evidence authenticity and upstream PIT admission remain the
responsibility of the US1-US5B evidence/admission chain and the caller.

## Non-goals

US6 performs no:

- network calls;
- provider/API acquisition;
- database writes;
- production integration;
- operational scheduling;
- broker interaction;
- trade-plan construction;
- position sizing or risk admission;
- paper execution;
- performance analysis;
- walk-forward evaluation;
- frozen-holdout evaluation.
