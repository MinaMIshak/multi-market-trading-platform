# Operator decisions recorded in the repository

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED.

## 2026-10-01: zero-cost EGX data strategy (TradingView)

Operator statement:

> Yes. Proceed with (a), (b), and (c). I explicitly approve the TradingView
> admission state as operator-accepted/no-contractual-licence, the backed-up
> TradingView acquisition/backfill on the operational DB with official EGX
> cross-check, and system-generated Paper/Shadow candidates with human review
> optional. Keep LIVE_MONEY disabled.

Context: no paid data subscriptions are allowed. Twelve Data EGX OHLCV needs a
paid plan, so it stays an optional provider.

What was approved:

(a) **Admission.** `tradingview_tvdatafeed_egx` gets the registry entitlement
`OPERATOR_ACCEPTED_UNLICENSED`. It is admitted for internal Paper/Shadow
research only. No contractual licence or entitlement exists and none is
claimed. Every operator view carries the licensing label
`NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED`. The legacy `tradingview_tvdatafeed`
declaration stays EVIDENCE_BLOCKED.

(b) **Acquisition.** TradingView daily backfill and daily acquisition run on
the operational database, after a consistent backup, with the official EGX
market-watch cross-check and quarantine.

(c) **Candidates.** The platform may generate Paper/Shadow candidates itself
when its evidence prerequisites pass: an admitted source, session-current
data, sufficient history, liquidity and the deterministic rules. Human review
is an optional approval layer, not a prerequisite. These are research and
simulation records: no orders, no broker, no live money.

Unchanged: LIVE_MONEY=DISABLED; no real-money execution path; paid sources
stay non-admissible.
