# Scheduler acquisition configuration

`EGX_ACQUISITION_CONFIG_PATH` opts a `paper_refresh` worker into an absolute-path
JSON configuration. OBSERVE remains the default and does not open this file.
Invalid configuration stops startup before database initialization, transport
construction or legacy secret access. With no path the existing execution-context
selection remains unchanged. Scan mode still defaults to disabled.

Schema version 1 requires exactly these keys:

- `schema_version`: integer 1.
- `provider`: exact lowercase free source identity; canonical, EODHD and EGID
  are excluded from this new free acquisition path.
- `lookback_days`: integer 1–1500, matching the scheduler's date subtraction.
- `targets`: nonempty array of objects containing `canonical_symbol` and
  `provider_symbol`. Both are exact uppercase identities; duplicates are rejected.
- `cost_contracts`: nonempty array of objects containing `symbol`, `start_date`,
  `end_date`, positive integer `units`, and nonempty `evidence`. Dates use
  YYYY-MM-DD. Each window must span exactly `lookback_days`, and every dated
  window must cover every target exactly once. Evidence must identify a real
  reviewed request-cost contract and contain no credentials.

Duplicate JSON keys, unknown fields and files larger than 1 MB are rejected.
The finite contracts are frozen at startup: a later session requires a matching
contract and worker restart with updated configuration. Costs are never
extrapolated. Runtime alias admission and exact cost preflight remain ahead of
fetching; durable quota reservations apply independently to repeated attempts.
Configuration does not establish rights, calendar truth, source health, universe
membership, data readiness, signals or fills.

**No free daily provider adapter is registered in this revision.** The default
factory rejects all source names. The preserved TradingView receipt is not a
repository transport implementation. Connecting an adapter requires its existing
reviewed evidence gates and a verified cost contract; do not replace the factory
with arbitrary dynamic imports or a legacy-provider fallback. Offline tests use
an explicitly injected fixture factory and are not operational evidence.

No deployment or scheduled acquisition is claimed. LIVE_MONEY=DISABLED.
