# Public UI information architecture

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED. A candidate is not a fill.

Principle: research output first, diagnostics complete but collapsed, and
operational audit detail in SYSTEM. Every value comes from a recorded report
or reader. A value the engine did not produce shows **UNKNOWN**; nothing is
recomputed or invented in the UI (`app/ui/dashboard.py`).

## TODAY · EGX (above the fold)

1. **Session context**: latest completed session, next expected session
   (labelled as an expectation: Friday/Saturday closed, holiday status not
   verified), data freshness counts, source with admission and licensing label
   (`NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED`), rule version, report time,
   **LIVE MONEY DISABLED**, and the prepared note (for example "Based on the
   Thursday 2026-10-01 close … ahead of … Sunday 2026-10-04").
2. **Summary cards**: scanned (evaluated), admitted-source symbols, admitted
   and session-current, ranked (scored), candidates, strong candidates.
3. **Candidate table** (STRONG_CANDIDATE, CANDIDATE, WATCHLIST):
   - columns: ticker, company, class, score, price, entry zone, stop, T1, T2,
     R:R, trend, 20-session momentum, volume ratio (✓ = confirmation), liquidity
     (average EGP traded per day), relative strength (pp against the
     ranked-universe median for the same session), history quality (bars,
     quarantined rows), freshness, evidence date, concise selection reason,
     warnings;
   - sortable by clicking a header, with filters by class.
4. **Classification rules** (collapsed legend).
5. **NO_TRADE symbols** (collapsed, with a reason histogram).

Below, collapsed: data coverage and readiness (evidence levels, blockers),
validated daily observations, security-master identities, and operational
receipt diagnostics (the legacy reviewed SWING path and every
verification window). Nothing is removed; it is layered.

## SWING

Session context, cards and the candidate table, then the **system-generated
Paper/Shadow candidates and their lifecycles** (PENDING_ENTRY until the entry
session), then the complete per-symbol ranking evidence (collapsed), and the
same collapsed diagnostics.

## PRE-SURGE

Session context, then the explainable **volume and momentum expansion
screen** (volume ≥ 1.5× its 20-session average with positive momentum; not a
prediction). The PreSurgeV7 attested-scorer contract is collapsed below and
labelled as having no validated inputs and no published scores.

## PERFORMANCE

Session context, then performance computed only from closed system-candidate
lifecycles (`INSUFFICIENT_SAMPLE` below 20 closed trades).

## SYSTEM

The detailed operational and audit page: build, runtime state and snapshot
verification, readiness dimensions, coverage breakdown, source registry with
licensing, calendar maintenance, ranking summary, scheduler, receipts.

## RESEARCH

Today it shows the financial-services methodology reference and sourced
research notes. **Not yet implemented**: macro, rates, FX, gold, Brent, news,
geopolitical, fundamentals and cross-market evidence. Each needs a
provenance-tracked source that is admitted under the same registry rules.
They stay out of TODAY.
