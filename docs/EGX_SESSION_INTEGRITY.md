# EGX session integrity (Phase 1, 2026-10-05)

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED.

## What was observed

The Sunday 2026-10-04 16:45 capture: status `Closed` (statusDate
2026-10-04T16:45:03), 223 rows, every `writeTime` on 2026-10-04 15:35, and
`lastTradeDate` = 2026-10-01 for 221 rows (2026-09-30 for 1, 2026-09-21 for
1). The session gate produced `bars = 0, NOT_TRADED_IN_SESSION = 223`.

## Root cause (field study over every stored capture)

`lastTradeDate` is **the date of the row's previous close (`prevClose`)**, not
the session of its prices:

| Capture | Volume equal to the primary bar on the `writeTime` date | on the `lastTradeDate` date | `prevClose` = primary close on `lastTradeDate` |
|---|---|---|---|
| 2026-09-30 post-close | 205/205 | 0/205 | 194/206 |
| 2026-10-01 post-close | 205/205 | 0/205 | 191/205 |
| 2026-10-01 intraday (open) | 0/204 (session incomplete) | 0/204 | 190/204 |

On Sunday the official rows matched TradingView's 2026-10-04 bars. For example,
COMI volume 1,606,773 and close 126.99 matched exactly, and CPCI and ACAP
closes matched. So the official feed was **not** serving stale Thursday
prices. The defects were:
1. The session gate dated rows by `lastTradeDate`, so it rejected every row on
   every capture since it was built: `bars = 0` on 2026-09-30 too. The label
   NOT_TRADED_IN_SESSION falsely implied that the stocks had not traded.
2. The cross-check accepted every row of a capture filed under session D
   without checking each row's session. It was correct only because the files
   happened to hold D's prices. A genuinely stale capture would have been
   compared across sessions.
3. The calendar job printed a calendar cutoff as `last_completed_session_date`.
   On a Sunday morning that is Saturday.

## Fixes

**Row observation** (`app/data/providers/egx_market_watch.py`,
`row_observation`). The times are kept separate:
- `capture_timestamp`
- `market_status_date`
- `source_write_timestamp`
- `provider_last_trade_date` (meaning: previous close)
- `observation_session_date`

A row is `SESSION_ALIGNED` with session D only when all of these hold:
- the status is Closed;
- the capture is after the 16:00 Cairo cutoff on D;
- the write date is D;
- the row has trades;
- the previous-close date is before D.

Otherwise its status is one of:
- `SECONDARY_STALE`: the write date is before D;
- `NOT_TRADED_IN_SESSION`: no trades or zero volume;
- `UNVERIFIED_AMBIGUOUS_SESSION`.

The capture time, the market status or `writeTime` alone never makes an old
observation current.

**Cross-check** (`app/data/daily_cross_check.py`, `load_session_evidence`):
- Only rows aligned with the primary session D are compared (MATCH /
  MINOR_DIFFERENCE / DISCREPANCY).
- A row that exists but is not a D observation gives
  `UNAVAILABLE_STALE_SECONDARY`, with no comparison and no quarantine. The
  primary bar is admitted under the unchanged primary rules.
- Without a capture for D the result is `UNAVAILABLE_NO_SECONDARY` (symbol
  verdict UNVERIFIED).
- The refresh records `cross_check_secondary`: the primary session, the
  secondary status, counts of aligned and not-aligned rows, and the capture's
  write and previous-close date distributions.

**Capture manifest:** it records `observation_times`, including the
distributions of `writeTime` and `lastTradeDate` and the meaning of
`lastTradeDate`.

**Calendar** (`app/data/official_calendar_maintenance.py`):
- `completion_cutoff_date` is the calendar bound. The old name is kept as an
  alias for the three callers that use it correctly, as an upper bound.
- `resolve_completed_session` returns:
  - `last_completed_session` with basis `VERIFIED_SESSION` or
    `EXPECTED_SESSION_UNVERIFIED`;
  - `last_verified_session` and `last_expected_session`;
  - `UNKNOWN` when calendar evidence is unreadable.
- Friday/Saturday and stored holidays or closures are never sessions.

Runtime check: Sunday 08:30 gives 2026-10-01 (VERIFIED). Sunday 17:00 and
Monday 08:30 give 2026-10-04 (EXPECTED_SESSION_UNVERIFIED) until official index
evidence verifies it.

**Universe scan vs ranking** (`app/egx_universe_scan.py`): these are
intentionally different scopes, and both are now exposed.

| Scope | Covers | Status meaning |
|---|---|---|
| Legacy universe scan | All 312 security-master equities, through the old SWING launch gates | `EVIDENCE_BLOCKED` = `LAUNCH_EVIDENCE_NOT_ATTACHED` (no launch configuration attaches per-symbol evidence) |
| EGX-RANK-v1 | Symbols with admitted primary series | – |

The job now reports `scope_semantics` and a `reconciliation` of security-master
equities with and without admitted primary series. The LIVE/SYSTEM scan view
explains the difference and points to the coverage breakdown.

## Current EGX session state (2026-10-05, before the Monday chain)

| Item | Value |
|---|---|
| Primary (TradingView) | 2026-10-04 bars exist at the source (EGX30, COMI, CPCI, ACAP checked). They are admitted by the Monday 08:30 chain under the unchanged primary rules once the calendar verifies 2026-10-04 |
| Official secondary | 2026-10-04, `AVAILABLE`, 223/223 rows session-aligned |
| Cross-check for 2026-10-04 | same-session comparison possible (sample: COMI MATCH; CPCI and ACAP MINOR_DIFFERENCE on the low) |

The stale path (primary 2026-10-04 with an official observation from an
earlier session gives secondary `STALE` and cross-check
`UNAVAILABLE_STALE_SECONDARY`) is covered by `tests/test_egx_session_integrity.py`.

## Tests

`tests/test_egx_session_integrity.py` covers:
- a valid Sunday session; Friday and Saturday weekends; holidays;
- a stale secondary, and current metadata with an old write;
- `writeTime` newer than `lastTradeDate`;
- no cross-session comparison, and no poisoning of a valid primary bar;
- a stale primary; target-session freshness; same-session verdicts;
- an unknown calendar; universe/ranking scope semantics.

The market-watch, TradingView and Twelve Data fixtures were updated to the
observed row format.
