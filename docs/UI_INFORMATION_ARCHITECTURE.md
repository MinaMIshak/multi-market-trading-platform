# Public UI information architecture

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED. A candidate is not a fill.

Principle: research output first, diagnostics complete but collapsed, and
operational audit detail in SYSTEM. Every value comes from a recorded report
or reader. A value the engine did not produce shows **UNKNOWN**; nothing is
recomputed or invented in the UI (`app/ui/dashboard.py`).

## TODAY · EGX (above the fold)

1. **Session context** (compact grid): latest completed session, next expected
   session (an expectation: Friday/Saturday closed, holiday status not
   verified), data freshness ("N of M current"), source, rule version, report
   time; below it **LIVE MONEY DISABLED**, the admission and licensing label
   (`NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED`) and the prepared note.
2. **KPI cards** (equal height, large number, small label): scanned, admitted
   source, admitted & current, ranked, candidates, strong candidates.
   "Admitted & current" counts validated daily rows whose source is ADMITTED and
   whose freshness is CURRENT. Freshness uses stored verified sessions and the
   fixed EGX weekend: a missing Friday/Saturday row counts as non-trading, while
   a missing Sunday–Thursday row stays unverified (UNKNOWN). This fixes the
   weekend "0 current" mismatch in the data path (`_daily_freshness`), not just
   in the display.
3. **Quick insights**: top idea (highest-scoring STRONG/CANDIDATE with its
   score, entry zone and stop), strong, candidate and watchlist counts, and the
   next evaluation session.
4. **Candidate table** (all classes):
   - filters: Actionable (default), All, Strong, Candidate, Watchlist, No trade;
     ticker/company search; sorting by any header;
   - layout: sticky header, zebra rows, right-aligned numerics, bold ticker with
     the company name beneath;
   - visible columns: class, score, price, entry zone, stop, T1, T2, R:R, trend,
     20-session momentum, volume ratio (✓ = confirmation), relative strength
     (pp against the ranked-universe median), liquidity;
   - per-row expandable evidence: selection or rejection reason, history
     quality, freshness, evidence date, warnings;
   - without JavaScript every row is shown.
5. **Classification rules** (collapsed legend) and **NO_TRADE reason histogram**
   (collapsed).

Theme: a single shared stylesheet (`app/ui/dashboard.py` STYLE) on every
product page and SYSTEM. It uses a calm slate base, one soft accent and muted
class colours. Amber is reserved for the safety label.

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

1. **Macro and cross-asset context** (`docs/MACRO_CONTEXT.md`):
   - US policy rate, 2-year and 10-year Treasury yields, the 2s10s curve, the
     broad USD index, EUR/USD and Brent;
   - each row shows its latest value and date, Δ 1 and Δ 20 observations,
     freshness and the publisher; provenance is collapsed;
   - context only.
2. The list of capabilities not yet sourced (gold, USD/EGP and the CBE rate,
   news, geopolitics, fundamentals), each with its reason.
3. The financial-services methodology reference and sourced research notes.

Macro context stays out of TODAY. It never changes a classification.
