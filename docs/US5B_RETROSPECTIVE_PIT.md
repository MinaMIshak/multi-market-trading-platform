# US5B Retrospective US PIT Daily Dataset

US5B composes the independent US historical-evidence boundaries into one
point-in-time-safe daily research dataset for a stable instrument.

The inputs are:

- US1 historical listing identity;
- US2 explicit historical sessions;
- US3 raw historical OHLCV;
- US4 complete bounded corporate actions;
- US5A complete exact-date research universe.

US5B performs no external acquisition and no production integration.

## Exact daily admission

For every requested calendar date US5B first resolves explicit session truth.

A closed session is explicitly recorded as an exclusion.

For an open or early-close session an exact-date universe snapshot is
required.

If the instrument is:

- absent from that exact snapshot, the date is excluded;
- present but ineligible, the date is excluded;
- present and eligible, exact identity and an exact raw daily bar are
  required.

An eligible open session with no exact raw bar fails closed as missing market
data.

Bar absence is never interpreted as a closed market.

## Universe and identity coherence

For an eligible member, US5B requires exact agreement between universe and
historical listing identity for:

- stable instrument ID;
- canonical symbol;
- listing MIC;
- security type.

This prevents current ticker projection and survivorship leakage from being
hidden during dataset assembly.

## Raw observations remain raw

US3 `RAW_UNADJUSTED` observations are preserved exactly in `rows`.

Provider-adjusted close remains audit/reference data and is never substituted
for executable OHLCV.

US5B produces a separate `split_adjusted` series for indicator/research use.

## Split-only transformation

The only executable transformation currently supported is explicit split
normalization:

`split-only-v1-decimal34`

For a split with explicit `new_shares / old_shares`:

- historical prices before the split are multiplied by
  `old_shares / new_shares`;
- historical volume before the split is multiplied by
  `new_shares / old_shares`.

Only splits actually crossed by the assembled series are applied.

The transformation uses an explicit Decimal context with precision 34, so
results are independent of ambient Decimal configuration.

Split-adjusted values are indicator/research values and are not execution
prices.

## Other corporate actions

`SYMBOL_CHANGE` and `DELISTING` are state/identity events, not automatic price
transforms.

Symbol-change facts are checked against the eligible historical identity
timeline on each available side of the event.

An eligible observation strictly after an explicit delisting date is rejected.

Cash dividends are explicit non-transforming events for this price-basis
dataset. The ex-dividend price move remains part of observed market-price
behavior. US5B does not infer total-return or dividend-reinvestment values.

The following actions have no approved cross-event price-series semantics yet:

- STOCK_DIVIDEND
- MERGER
- SPINOFF
- RIGHTS
- OTHER

If the assembled price series has observations both before and on/after one
of these unsupported events, US5B fails closed.

No total-return, dividend-reinvestment, merger-conversion, rights-value, or
spinoff-value rule is inferred.

## Complete corporate-action truth

US4 coverage must contain the entire requested PIT interval.

A covered date with no actions is valid complete negative evidence.

Missing action coverage is not equivalent to no corporate actions and is
rejected.

## Point-in-time horizon

All dependency histories must use the same `decision_at`.

Every dependency must have been built no later than the US5B research build.

Requested coverage cannot extend beyond the US-local decision date.

Each upstream evidence boundary remains responsible for proving that its
historical source information was safely knowable by that decision horizon.

## Determinism and provenance

The resulting dataset records the semantic identities of all five admitted
historical inputs.

Its semantic identity includes:

- stable instrument ID;
- calendar MIC;
- requested date range;
- decision time;
- transformation contract;
- five upstream history identities;
- raw admitted rows;
- split-adjusted rows;
- explicit date exclusions.

`research_built_at` is deliberately excluded from semantic identity.

## No survivorship projection

A security that was historically eligible remains represented even if it
later delisted, merged, failed, or disappeared from the current provider
universe.

Likewise, a later constituent cannot be projected backward into an earlier
exact-date universe.

## Non-goals

US5B performs no:

- network access;
- provider API calls;
- database writes;
- scheduler changes;
- broker interaction;
- live execution;
- strategy optimization;
- total-return calculation.

US5B establishes research input truth. It does not establish strategy
profitability.
