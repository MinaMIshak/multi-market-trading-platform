# Nasdaq halt-feed qualification — EXPERIMENTAL / PAPER ONLY

## Decision

The free Nasdaq Trader halt feed is a candidate source of affirmative US
issue-specific halt observations. It is **not qualified as a complete active-halt
register**, historical suspension package, or tradability clearance. No feed was
requested, security status admitted, watchlist frozen, or empirical trade scored.
Canonical session and execution admission remain NO_GO.

## Retained documentation

Original public documentation bytes are retained only at
`/home/egx-agent/research-data/er1c-us-pilot/nasdaq-halt-qualification-20260913T061309Z`.
The three requests returned HTTP 200 without redirects. `manifest.json` records
source and resolved locators, exact sizes, SHA256 and actual UTC request/receipt
bounds, spanning 2026-09-13T06:13:09.776214+00:00 through
2026-09-13T06:13:11.443263+00:00. `manifest.sha256` binds that manifest.
These are current documentation editions identified by receipt and hash, not
proof of historical publication or feed availability.

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `rss_documentation.html` | 50420 | `7acea06e83f9d40cdd0cb95cd2ed9d04601cfa068fc414db213c0a3a38e761a2` |
| `rss_terms.pdf` | 75440 | `9f3e25671be08d33b81785dac0eae63fc374904debcf2452aa1752964b3751b1` |
| `rss_queries.html` | 4369 | `bbe019d76538d86e84826b06b35da110f56ea3e709621828985e15422f8d3217` |

The [service documentation](https://www.nasdaqtrader.com/Trader.aspx?id=TradeHaltRSS)
describes a free feed covering Nasdaq-listed and other exchange-listed securities,
allows a reader or custom process, and limits requests to no more than one per
minute. This cycle acquired documentation only and started no polling service.

The [published terms](https://www.nasdaqtrader.com/content/administrationsupport/agreementstrading/THRSSFeedTermsCond.pdf)
state that accessing/using the feed accepts the terms. They restrict editing,
modification and misrepresentation, disclaim accuracy/timeliness/completeness,
and include indemnification and revision provisions. This qualification does
not establish permission for derived-feed redistribution or indefinite archival
reuse. Retention and transformation scope remain unresolved before integration;
no subscription, paid service or separate agreement was entered into here.
The PDF was retained verbatim; its text was reviewed through the browser's PDF
extraction because local PDF extraction tools were unavailable.

## Safety-critical query semantics

The official [query examples](https://www.nasdaqtrader.com/snippets/tradehaltaccordion.html)
distinguish current-day starts, specified halt dates, and resumption dates.
Their current-day example omits securities whose halts began on the preceding
day. Their historical example assigns a multi-day halt to its original start
date. Consequently, querying today's halt starts is insufficient to determine
which securities are currently halted. This is an inference from the documented
selection rules, not a test of a retrieved feed response.

- A missing symbol, empty response, failed retrieval, or vendor OHLCV row cannot
  clear a prior halt or prove an exchange session open.
- A later historical query can contain subsequent resumption information. Its
  current receipt cannot supply pre-decision knowledge of that resumption.
- The combined halt/resumption example must not be assumed to specify a date
  range or complete interval coverage; its filtering semantics need separate
  verification before implementation.
- Query examples from 2010 are illustrative documentation, not retained market
  observations and not evidence of archive depth through the frozen 2022 pilot.
- Symbols and reported market labels need exact-date stable-identity and venue
  bindings. A cross-market feed does not itself prove XNYS roster completeness.

## Next bounded work

Inspect the linked field definitions and retained terms to resolve usable
retention/transformation scope. If admissible, predeclare a bounded acquisition
and capture immutable feed bytes with actual receipt clocks, field locators and
correction lineage. Validate date/time semantics and distinguish initial halt,
quote resumption and trade resumption before constructing canonical facts.
Maintain unresolved prior halts across day boundaries; unknown starting state
must remain unknown until affirmative evidence resolves it. A stale snapshot
cannot clear status for a later execution decision.

This source can complement exact-date calendar evidence and authoritative
issue-specific notices, including the retained Twitter suspension notice. It
cannot replace those notices, complete actions, historical membership, approved
PIT reviews, costs, authentic execution observations or strategy validation.
Historical ER1C and forward collection remain open research work.
