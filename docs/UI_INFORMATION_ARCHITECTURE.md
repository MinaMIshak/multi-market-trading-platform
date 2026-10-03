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

## US (TODAY / SWING / PRE-SURGE / PERFORMANCE, market US or ALL)

US output uses the same components from the US-RANK-v1 report (`app/ui/us.py`):
- session context: NYSE session, next expected NYSE session;
- KPI cards: acquired / current counts come from the US report;
- quick insights and the US candidate table (`US candidates and watchlist`);
- SWING: US Paper/Shadow lifecycles;
- PRE-SURGE: the US volume screen;
- PERFORMANCE: US performance;
- collapsed US data evidence: universe rule, session verification, Yahoo
  cross-check, costs.

Without a verified US report the page says UNAVAILABLE and claims no
readiness.

## Context column (FUSION-v1)

The candidate tables carry a sortable **Context** column:
- confidence HIGH / MEDIUM / LOW / UNKNOWN, with the bounded adjustment and
  the context score;
- the per-row evidence lists the rule notes, official catalysts (recent EGX
  disclosures or results) and the latest disclosed EGX result.

The class and the market-data score are never changed by context.

## RESEARCH

Built from the context report (`app/ui/context.py`; sources in
`docs/SOURCE_DECISION_MATRIX.md`):

1. **Market regime**: US and EGX equity regimes, context risk per market with
   its flags, Fed stance, EGP, gold, Brent, Suez and the curve, each with its
   rule.
2. **Rates and monetary policy**: Fed target range, effective rate, 2y/10y,
   2s10s, the FRED vs BIS cross-check, Egypt (IMF, lagged and labelled with
   its age), and Fed monetary-policy releases.
3. **FX**: USD/EGP market rate (ICE), the reference rate and their
   reconciliation, the broad dollar index, EUR/USD. The official CBE rate is
   BLOCKED.
4. **Gold**: OANDA spot vs TVC composite (reconciled).
5. **Brent / oil**: front-month futures vs EIA dated spot (the gap is
   reported, not reconciled).
6. **Equity indices**: EGX30, S&P 500, VIX, and EGX30 in USD terms.
7. **News and catalysts**: EGX official disclosures and Fed releases. SEC
   filings are BLOCKED until a contact is set.
8. **Geopolitical context**: PortWatch chokepoints (Suez, Bab el-Mandeb,
   Hormuz, Cape), OFAC SDN counts, and GDELT narrative, labelled as narrative.
9. **Fundamentals**: EGX financial-statement disclosures (parsed or
   UNPARSED_LAYOUT). US SEC XBRL status.
10. **Cross-market evidence**: 60-common-session return correlations.
11. Sources, freshness and provenance (collapsed). If no context report
    exists, the macro panel is the fallback.

Context never changes a classification. Media narrative is never a regime,
fusion or ranking input.
