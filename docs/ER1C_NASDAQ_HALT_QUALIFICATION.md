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
modification and misrepresentation absent a separate agreement, disclaim
accuracy/timeliness/completeness, and include indemnification and revision
provisions. They do not expressly grant the archival retention and transformation
needed to preserve immutable feed versions and produce normalized research facts.
Public access is therefore insufficient permission for this workflow. Raw
retention and transformation remain **UNRESOLVED**, and feed acquisition is
**NO_GO** unless an applicable official policy or agreement resolves both. No
subscription, paid service or separate agreement was entered into here.
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

## Field-definition qualification

The linked official field definitions and current trading-halts reference were
retained in a separate documentation-only package at
`/home/egx-agent/research-data/er1c-us-pilot/nasdaq-halt-fields-20260913T061742Z`.
The manifest SHA256 is
`32dffece0ea940ac4809d007546eb0c90ed0f59b2825b6247f95d1ee4a2769be`.
The field definitions are 73,633 bytes with SHA256
`f3d84d5e2f84d88a49904f6132dade46abdd7199274bf6b86e89ec4d101493b6`;
the trading-halts reference is 47,392 bytes with SHA256
`2d5105fee27893b3b293527c3cdb28a12528571bdc7704f17818778201965b6d`.
Both official HTTPS requests returned HTTP 200 without redirects. These are
current documentation receipts, not halt observations or historical editions.

The definitions separate the initial halt date/time from the resumption date,
scheduled quotation resumption time and scheduled trading resumption time. The
current-halts page states that displayed halt times use Eastern Time. A scheduled
resumption time does not prove that quoting or trading actually resumed, so it
cannot alone authorize a simulated fill. The reason code may change when quoting
resumes; consumers must preserve observed versions rather than overwrite the
initial state.

The `Mkt` field only distinguishes `NASDAQ` and `Non-NASDAQ`. `Non-NASDAQ` is not
an XNYS identifier and cannot bind a mutable symbol to a stable XNYS security.
Exact-date identity and venue evidence remain mandatory. The offline auditor
`tools/audit_er1c_nasdaq_halt_fields.py` verifies the closed inventory, hashes,
locators, UTC receipt ordering, media types and these semantic anchors. Its pass
means only that the retained documentation matches the qualified scope.

No live feed was called. Canonical security status, XNYS identity binding,
actual execution resumption, historical availability and complete active-halt
coverage remain **NO_GO**.

`tools/audit_er1c_nasdaq_halt_terms.py` binds this decision to the exact three-file
package and reviewed terms SHA256. It rejects changed editions, redirects,
tampered bytes, expanded inventory, non-UTC receipts and any claim that the
current documentation proves historical availability. An integrity pass does
not grant rights: its output keeps retention and transformation unresolved and
both feed acquisition and canonical admission at **NO_GO**.

## Next bounded work

Resolve usable retention/transformation scope from a further applicable official
policy or agreement. If admissible, predeclare a bounded acquisition
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
