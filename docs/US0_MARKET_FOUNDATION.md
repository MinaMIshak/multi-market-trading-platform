# US0 — US Market Foundation Contracts

Status: software foundation only.

US0 introduces market-specific semantic contracts for future US historical
research.  It does not acquire market data, select a vendor, validate a real
historical dataset, create a trading strategy, simulate trades, or enable live
execution.

## Architectural boundary

The existing operational implementation is EGX-specific in several important
places, including reference contracts, current security-master identity,
calendar defaults, and point-in-time daily derivation.

US0 therefore does not refactor the EGX implementation into a generic
multi-market hierarchy.  US semantics live under `app/us/` until actual shared
behavior is demonstrated by both markets.

Generic infrastructure may be reused later where its semantics are genuinely
market-independent.

## Stable security identity

A US ticker is not an instrument identity.

`USListingIdentity` binds an exact market date to:

- stable `instrument_id`;
- canonical symbol;
- listing MIC;
- security type;
- provider symbol;
- provider name;
- provider-specific stable instrument key.

A symbol may change while the stable `instrument_id` remains the same.

US0 does not infer historical ticker continuity from current listings, company
names, price series, or ticker similarity.

## Universe truth

`USUniverseSnapshot` is an exact-date complete snapshot.

It provides no:

- current-membership projection into history;
- forward fill;
- latest-wins behavior;
- survivorship reconstruction from present-day constituents.

Instrument identity must be unique inside a snapshot.  A dated listing key is
the `(listing_mic, canonical_symbol)` pair rather than ticker alone.

## Calendar truth

The canonical US timezone is `America/New_York`.

`USSessionRecord` represents explicit historical session facts:

- `REGULAR`;
- `EARLY_CLOSE`;
- `CLOSED`.

Trading sessions require explicit UTC open and close timestamps.  Closed
sessions contain no synthetic hours.

US0 deliberately does not infer:

- weekends;
- exchange holidays;
- DST offsets;
- normal opening hours;
- early-close times;
- session existence from observed price bars.

The timestamps supplied by future evidence must resolve to the declared
`market_date` in `America/New_York`.

## Corporate actions

`USCorporateActionEvent` can represent:

- split;
- cash dividend;
- stock dividend;
- merger;
- spinoff;
- symbol change;
- delisting;
- rights;
- other events.

A split requires an explicit `new_shares / old_shares` ratio.  Split terms are
never inferred from price jumps.

A symbol change requires explicit old and new symbols.

A cash dividend requires an explicit USD amount.

Representation of an event does not mean that future price transformation or
execution logic supports that event.  The initial retrospective daily
transformation is expected to support split-only indicator adjustment and to
fail closed on relevant unsupported action types.

`USCorporateActionCoverage` describes complete coverage for one stable
instrument over an explicit date interval.  Different corporate actions may
legitimately share an effective date; only duplicate event identities are
rejected.

## Historical evidence boundary

US0 models semantic facts only.

It does not pretend that a semantic object proves when a source fact became
historically available.  A later US evidence/admission layer must bind
immutable source artifacts and these semantic facts to the existing
retrospective historical-evidence concepts, while preserving the distinction
between:

- source/effective time;
- historical availability;
- local receipt;
- review;
- research build time;
- decision time.

No operational EGX reference repository or point-in-time repository is used by
US0.

## Explicit non-goals

US0 contains no:

- provider calls;
- web requests;
- database writes or schema changes;
- operational scheduler changes;
- broker integration;
- paper or live orders;
- position sizing;
- strategy signals;
- profitability claims;
- real historical data;
- source/vendor approval.

The milestone establishes only the semantic boundary needed before historical
US evidence and point-in-time research can be implemented.
