# EGX per-equity source evaluation (2026-09-30)

Goal: the broadest truthful EGX per-equity daily coverage that can be
*admitted* for internal Paper/Shadow use. Evidence came from the server
(host egress available), using one read-only request per probe. No accounts
were created, no terms were accepted, and no anti-bot barrier was bypassed.

## Matrix

| Source | Coverage seen | Depth | Semantics / quality | Access | Usage terms discoverable | Verdict |
|---|---|---|---|---|---|---|
| **EGX official `market-watch`** (`beta.egx.com.eg` BFF) | 217 main-market common stocks; all map to canonical instruments via egid Reuters aliases | Forward only (one session per capture) | Official exchange values; ISIN, OHLC, prev close, volume, value, trades. Open-session rows carry today's prices under the previous `lastTradeDate`, so a closed-session gate is required | Anonymous | None linked on the beta site; `www.egx.com.eg` unreachable | **Best technical source. EVIDENCE_BLOCKED on rights.** Adapter, gate and evidence capture implemented. |
| EGX official index API (`egx_official_public`) | 3 calendar indices | Since inception | Official | Anonymous | As above | Calendar evidence only (existing). Not per-equity. |
| EGID `getSymbolHistory` | Per equity | History | Delayed feed | HTTP 401 without credentials | Subscription terms | EVIDENCE_BLOCKED (authentication; accepting terms is out of scope) |
| Yahoo Finance chart API (`*.CA`) | COMI, HRHO, ETEL, SWDY respond | ~1 month requested; bars include today's open session | Metadata inconsistent: every symbol is `MUTUALFUND` with `regularMarketTime` 2024-07-23, while bars reach 2026-09-30 | Unofficial, keyless | Yahoo terms: personal, non-commercial use | Rejected: unreliable semantics, and rights not established |
| Stooq CSV | — | — | — | JavaScript proof-of-work anti-bot challenge | — | Rejected: automated access is actively blocked, and it will not be bypassed |
| TradingView (`tvdatafeed`) | Per equity (COMI reviewed by the operator) | History | Unofficial client | Unofficial | Rights unreviewed | Existing; EVIDENCE_BLOCKED (the COMI operator review does not assert rights) |
| EODHD | Per equity | History | EOD | Paid token | Paid subscription | Not admissible (the platform must not require paid data) |
| Alpha Vantage | Unknown | — | — | API key required (demo key refused) | Sign-up means accepting terms | Not pursued: requires accepting terms on the operator's behalf |
| Twelve Data | Unknown | — | — | HTTP 401 without key | Sign-up | Not pursued (as above) |
| Marketstack | Unknown | — | — | HTTP 401 without key | Sign-up | Not pursued (as above) |
| Polygon | US only | — | — | Key | — | Not applicable to EGX |

## Decision

The official EGX `market-watch` feed is the selected EGX per-equity source.
It is official, covers all 217 main-market common stocks, and maps with no new
identity data. It stays **EVIDENCE_BLOCKED** until its usage rights are
reviewed. No other candidate is both technically sound and free of a rights,
terms or payment blocker.

## Universe

No dated authoritative membership source was found.
- `listed-stock` and `sme-stock` are search categories in the site's client,
  not membership lists.
- The `market-watch` row set is a dated snapshot of the securities the
  exchange currently displays. That supports universe evidence, but its
  completeness (suspended or delisted names, the SME market) is unverified,
  and it is rights-blocked.

`authoritative_universe` therefore stays UNKNOWN. The 319 security-master
identities are not promoted.
