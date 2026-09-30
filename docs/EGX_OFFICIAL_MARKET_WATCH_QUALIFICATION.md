# EGX official market-watch: technical qualification (2026-09-30)

Status: **EVIDENCE_BLOCKED**. Registry provider `egx_official_market_watch`:
`ANONYMOUS_PUBLIC`, entitlement `NOT_ESTABLISHED`, delay `END_OF_DAY`.
This record is technical evidence only. Public accessibility is not a usage
right, and nothing here admits the source.

## Why it matters

This is the only free per-equity EGX observation path found so far. Earlier
candidates: EGID history needs authentication (401), EODHD is paid, and
TradingView is an unofficial client with unreviewed rights
(`docs/ER1B_FREE_SOURCE_QUALIFICATION.md`). Blind path guessing on
`beta.egx.com.eg` hit the WAF. Endpoint names were not previously known.

## How the endpoints were found

On 2026-09-30, from the server (host egress allowed), read-only:

1. The public Next.js bundles served by `https://beta.egx.com.eg` were read
   statically. The site's client calls a generic backend-for-frontend (BFF) at
   `/api/bff/egx/<name>` with headers `x-egx-bff-request: 1` and
   `x-egx-bff-client: web`. This is the same pattern as the already-reviewed
   `egx_official_public` index provider (`app/data/providers/egx_official.py`).
2. Endpoint names referenced in those bundles include `market-watch`,
   `market-status`, `listed-stock`, `sme-stock`, `market-summaries`,
   `index-since-inception` and `index-data`.
3. One GET each, after loading `/en` for a session cookie:

| Endpoint | Result |
|---|---|
| `market-watch` | HTTP 200 JSON, `totalCount` 216, 5 pages of 50. The rows sampled all had `marketCode` `NOPL`. Fields include `isin`, `reuters`, `name`, `sector`, `openPrice`, `high`, `low`, `closePrice`, `prevClose`, `lastPrice`, `volume`, `value`, `trades`, `lastTradeDate`, `writeTime`, `withHistory` and index-membership flags (`egx30Cap`, `egx70`, `egx100`, …). While the session was open (11:11 Cairo), `lastTradeDate` was 2026-09-29 for every sampled row. |
| `market-status` | HTTP 200 JSON: `status` `Open`, `statusDate` `2026-09-30T11:11:09`. |
| `listed-stock` | A plain GET was rejected by the WAF ("Request Rejected"). The client probably uses different parameters or methods; not pursued. |

Only the first page of `market-watch` was read. No data was stored, and no
adapter or scheduled acquisition exists.

## What it would provide, if admitted

- A per-equity OHLCV observation for the last completed session, keyed by ISIN
  and Reuters code, for about 216 securities (all instruments on the page, not
  only equities). This accumulates forward. It is not history: `withHistory`
  suggests a per-stock history endpoint that has not been identified.
- `market-status` as same-day official session evidence.
- A dated snapshot of the securities the exchange currently publishes. This
  is supporting evidence for the universe, not an authoritative membership
  list, until its semantics are confirmed.

## Semantics finding: `lastTradeDate` does not date the prices

At 11:39 Cairo on 2026-09-30, with the session open, every row had
`lastTradeDate` 2026-09-29 but carried today's live values. For example,
ACAP: `prevClose` 7.91, `openPrice` 7.91, `closePrice` 7.70, `lastPrice` 7.75,
`chgPer` −2.65 (7.70 against 7.91). Dating rows by `lastTradeDate` would
record today's partial session as yesterday's completed bar. The adapter
therefore never uses `lastTradeDate` alone.

## Coverage and mapping (2026-09-30)

With parameters `Page`/`PageSize` (taken from the site's own client code), all
5 pages returned 217 rows, matching `totalCount`, with 217 unique ISINs, all
`symbolType` C and `marketCode` NOPL. **All 217 Reuters codes map to canonical
instruments** through the existing egid `REUTERS_RAW` aliases. No new mapping
data is needed. If admitted, observed coverage would go from 1 symbol to 217.

## Implementation (blocked, not scheduled)

- `app/data/providers/egx_market_watch.py`: session warm-up, BFF headers,
  full pagination, retry with exponential backoff (429/5xx/network), WAF HTML
  detection, shape checks, total-count and duplicate-ISIN checks.
  `completed_session_bars` produces neutral daily rows (`date`, `open`, `high`,
  `low`, `close`, `volume`; no `adjusted_close`) only when `market-status` is
  closed, the capture is on the session date after 16:00 Cairo, and each row's
  `lastTradeDate` equals that date. Other rows are rejected with a reason
  (`NOT_TRADED_IN_SESSION`, `OHLC_INCONSISTENT`, …), never repaired.
- `python -m app.data.egx_market_watch_capture --out-root <dir>`: stores the
  raw pages and a manifest (hashes, counts, gate verdict) as an immutable,
  read-only evidence directory. It writes no database and admits nothing.
- The existing per-symbol daily refresh path expects provider aliases and 260
  bars of history, so it does not fit a forward, cross-sectional source.
  Canonical storage of market-watch bars (one artifact per session) is the
  follow-up once rights are reviewed and the closed-session semantics are
  confirmed.
- Live check at 11:58 Cairo: 217 rows in 5 pages; gate verdict
  `SESSION_NOT_CLOSED:Open`, as intended.

## Open questions before any admission

1. **Usage rights (blocking).** The beta site links no terms of use or
   data-licensing page. `www.egx.com.eg` was unreachable from the host. Whether
   this data may be stored and used for internal, non-redistributed
   paper/shadow evaluation is unknown.
2. Field semantics: whether `closePrice` is the official session close or a
   last trade; the timing of `writeTime`; and how suspended or untraded
   securities appear (`lastTradeDate` older than the last session).
3. Completeness: all pages, SME market rows (`sme-stock`), and whether the
   216 rows cover every listed equity.
4. Rate or behaviour limits of the BFF/WAF for one daily post-close read.

## Owner and action to unblock

- **Operator/business (rights):** obtain written confirmation from EGX, or a
  reviewed reading of EGX's published terms, that storing the public
  market-watch end-of-day values for internal paper/shadow use is permitted.
  Then set `entitlement=REVIEWED_PAPER_SHADOW` with the review reference as
  evidence. This is the only change that admits the source. It must not be
  inferred from availability.
- **Agent (technical, safe now):** implement a fixture-tested adapter and
  canonicalization into daily artifacts that stay `EVIDENCE_BLOCKED` until the
  entitlement is reviewed. Do not schedule acquisition before the rights
  review, unless the operator explicitly approves blocked-evidence collection.
