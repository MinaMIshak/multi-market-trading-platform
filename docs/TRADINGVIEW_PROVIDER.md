# TradingView (tvdatafeed protocol): zero-cost EGX daily acquisition

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED.

Operator decision (2026-10-01, zero-cost strategy): TradingView via the
tvdatafeed protocol is the intended primary EGX daily source, with official
EGX market-watch as secondary verification. Twelve Data stays as an optional
provider (EGX OHLCV needs a paid plan).

## Rights and admission (truthful status)

- Access: anonymous, unofficial client of TradingView's public chart
  websocket (no login, no token). Data vendor as resolved by TradingView: ICE.
- There is **no contractual licence or entitlement**, and none is claimed.
- Registry status: `tradingview_tvdatafeed_egx` has entitlement
  `OPERATOR_ACCEPTED_UNLICENSED`. It is **ADMITTED** for internal
  Paper/Shadow research by the operator's explicit decision of 2026-10-01
  (`docs/OPERATOR_DECISIONS.md`). Every operator view carries the label
  `NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED`. The legacy
  `tradingview_tvdatafeed` declaration stays EVIDENCE_BLOCKED. Paid sources
  stay non-admissible.

## Pipeline

| Step | Implementation |
|---|---|
| Discovery | TradingView symbol search (`exchange=EGX`, stocks), paged; raw pages stored hashed |
| Mapping | Exact ISIN (`app/data/provider_mapping.py`); TradingView lists the ISIN for every EGX stock |
| Fetch | `tools/tradingview_fetch.py` runs under the TradingView tooling interpreter and returns the raw websocket stream unmodified |
| Parse | `app/data/providers/tradingview.py`: `~m~` frames decoded as JSON; `symbol_resolved` must be EGX / Africa/Cairo / EGP / stock; epochs converted to Cairo session dates; duplicates and non-finite values fail closed; bars after the target session dropped |
| Identity | The TradingView-resolved ISIN must equal the mapped ISIN (`RESOLVED_ISIN_MISMATCH`) |
| Store and validate | The reviewed daily pipeline: immutable raw bytes, canonical validation with row quarantine, admission policy (newest bar equals the latest VERIFIED session, ≥ 260 valid bars), immutable idempotent artifacts |
| Verify | Cross-check of the session bar against a completed-session official market-watch capture by ISIN (0.5 % O/H/L/C); a discrepancy quarantines the symbol with evidence |
| Guards | Integrity before and after, lifecycle tables unchanged, lock, JSONL log, `last-run.json`, per-symbol outcomes |

Price semantics: TradingView's series is **split-adjusted**
(`price_adjustment: splits` in every artifact's provenance). It is not mixed
with other providers' series. The native stream is kept per fetch under
`<state dir>/native/`.

## Evidence (2026-10-01)

- Discovery: 299 EGX stocks; mapped to the 312-equity security master by ISIN:
  **295 matched**, 2 ambiguous (never mapped), 0 provider-only, 0 invalid, 15
  known equities not listed (mostly `_R*` rights lines, an ETF and a fund).
  16 matches use a different TradingView ticker (for example AIHC is `AIH`).
  This is reported, and fetches use TradingView's own symbol.
- Live trial on a copy of the operational DB (COMI, ABUK, ACAP): stored, 660
  valid bars each, 0 quarantined, window 2024-01-03..2026-09-29, integrity `ok`.
- Cross-source reconciliation for 2026-09-30 (TradingView against the official
  post-close capture): open, high, low and volume identical for all three;
  close identical for COMI and ABUK, ACAP 7.56 against 7.55 (0.13 %).
- Official market-watch dating: the 2026-10-01 morning `prevClose` equals the
  2026-09-30 post-close `closePrice` for 212 of 213 ISINs (one mismatch,
  `EGS69491M015`). Post-close captures therefore carry that session's values,
  even though `lastTradeDate` lags.

## Commands

```
python -m app.data.tradingview_refresh \
  --db-path /home/egx-agent/research-data/paper-shadow-operational/platform.db \
  --data-root /home/egx-agent/research-data/paper-shadow-operational/data \
  --state-dir /home/egx-agent/er1-autopilot/state/tradingview \
  --tv-python /home/egx-agent/research-data/paper-shadow-operational/tooling/tradingview-venv/bin/python \
  --market-watch-evidence /home/egx-agent/er1-autopilot/state/market-watch-evidence
```

`--symbols` limits the run, `--n-bars` sets the history depth (default 750),
and `--lookback-days` sets the stored window (default 1000). Re-running on the
same day is idempotent.
