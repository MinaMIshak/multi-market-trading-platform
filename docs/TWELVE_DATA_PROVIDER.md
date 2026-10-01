# Twelve Data (XCAI): primary EGX daily provider

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED.

Operator decision (2026-10-01):
- **Primary** EGX daily OHLCV: Twelve Data, MIC `XCAI` (provider `twelve_data`).
- **Secondary verification**: official EGX market-watch
  (`egx_official_market_watch`). It is used only to cross-check the latest
  completed session and is never stored as bars. Its own storage and
  algorithmic rights are unresolved.
- **TradingView / tvdatafeed**: EVIDENCE_BLOCKED. It is not used for scans,
  candidates or market truth. The registry already refuses it, and the 2026-09-26
  COMI review records that it does not assert usage rights.

Registry status: `twelve_data` is `AUTHENTICATED` / `NOT_ESTABLISHED` /
`END_OF_DAY`, so **EVIDENCE_BLOCKED**. Bars it stores are visible as
evidence but never feed candidates until admission (below).

## What exists

| Piece | Module |
|---|---|
| Adapter: `/time_series` 1day, `mic_code=XCAI`, `timezone=Africa/Cairo`; neutral rows; batching (`prefetch`); credit limiter (per minute and per run); retry with backoff on 429/5xx/network; no retry on 401/403; key redacted from every recorded URI | `app/data/providers/twelve_data.py` |
| Exact ISIN mapping (check digit; exact / `.EGP` suffix / ambiguous / provider-only / unmatched) and idempotent alias application | `app/data/twelve_data_mapping.py` |
| Official market-watch cross-check of session D (MATCH / DISCREPANCY / UNVERIFIED, 0.5 % tolerance on O/H/L/C); a discrepancy quarantines the symbol with evidence | `app/data/daily_cross_check.py` |
| Orchestrator: reference, mapping, aliases, latest VERIFIED session, batched prefetch, one reviewed `DailyRefreshJob` per symbol, cross-check, integrity and lifecycle guards, lock, logs and status | `app/data/twelve_data_refresh.py` |
| Tests (fake provider, temporary platform DB, end-to-end) | `tests/test_twelve_data.py` |

The reviewed daily pipeline does the storage and validation:
- raw bytes are stored immutably (`data/raw/twelve_data/...`);
- canonical validation quarantines rows that fail OHLC, positivity, ordering
  or duplicate checks;
- the admission policy requires the newest bar to equal the verified session
  and at least 260 valid bars (`--minimum-valid-bars`);
- artifacts are immutable and idempotent per (symbol, snapshot date). An
  identical same-day re-run changes nothing.

## Mapping (live, 2026-10-01)

Twelve Data's XCAI `symbol` is the ISIN. The egid security master's
`source_symbol_code` is the ISIN for all 312 equities, and all 312 pass the
check digit.

| Category | Count |
|---|---|
| Known equities (security master) | 312 |
| Provider XCAI rows | 266 (265 Common Stock, 1 REIT) |
| Matched | **266**: 246 exact ISIN, 20 `<ISIN>.EGP` |
| Ambiguous | 0 |
| Provider-only | 0 |
| Known equities not offered by Twelve Data | 46 |
| Invalid provider symbols | 0 |

The full report (every row) is `/home/egx-agent/er1-autopilot/state/twelve-data/mapping/latest.json`.
The raw reference list is stored read-only, with its SHA-256, under `.../twelve-data/reference/`.

## Activation: supplying the key is the only technical step

1. Put the key in a private file:
   `install -m 600 /dev/null /home/egx-agent/er1-autopilot/state/twelve-data/api-key`,
   then write the key into it. The file must be owned by egx-agent and not
   readable by group or others; the job refuses it otherwise.
2. Run a historical backfill once (credits: one per symbol per request; at 8
   per minute this takes about 35 minutes for 266 symbols):

```
cd /home/egx-agent/er1-autopilot/state/releases/<sha> && env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 TZ=Africa/Cairo EGX_TWELVE_DATA_API_KEY_FILE=/home/egx-agent/er1-autopilot/state/twelve-data/api-key EGX_BUILD_REVISION=<sha> /home/egx-agent/work/egx-trading-platform-us/.venv/bin/python -m app.data.twelve_data_refresh --mode backfill --start-date 2025-01-01 --db-path /home/egx-agent/research-data/paper-shadow-operational/platform.db --data-root /home/egx-agent/research-data/paper-shadow-operational/data --state-dir /home/egx-agent/er1-autopilot/state/twelve-data --market-watch-evidence /home/egx-agent/er1-autopilot/state/market-watch-evidence
```

   Use `--credits-per-minute` / `--daily-credit-budget` / `--batch-size` to
   match the plan. Use `--symbols ACAP,ABUK` for a first small trial.
3. Install the daily entry (the same command with `--mode daily`, without
   `--start-date`). It runs between the calendar job and the universe scan.
   With a 35-minute run, move the scan and publication later:
   calendar 18:17 → `# EGX_TWELVE_DATA_DAILY` 18:25 → universe scan 19:10 →
   publication 19:30.
4. Check `/home/egx-agent/er1-autopilot/state/twelve-data/last-run.json`:
   status, outcomes (`STORED`, `REJECTED`, `FETCH_FAILED`,
   `QUARANTINED_CROSS_CHECK`), cross-check verdicts, credits remaining,
   integrity, and lifecycle tables unchanged.

## Admission: a rights decision, not a technical one

A working key does not admit the source. Admission requires:
1. the Twelve Data plan that actually covers XCAI end-of-day data, and its
   terms, reviewed for internal, non-redistributed Paper/Shadow use;
2. the declaration in `app/data/source_admission.py` changed to
   `entitlement=REVIEWED_PAPER_SHADOW`, with the review reference as
   `evidence`, as a reviewed commit.

If the plan that covers XCAI is paid, the declaration must say
`PAID_SUBSCRIPTION`. Policy refuses to admit paid sources ("final operation
must not require paid data"), so that would be an explicit operator
decision to change the policy.

## After admission: the chain

backfill → freshness (verified sessions) → universe scan → candidates →
Paper/Shadow. Admitted, session-current artifacts appear in the coverage
breakdown (`admitted_current_symbols`). The universe scan verifies through
SWING only symbols that have a launch input. By contract
(`app/paper/swing_launch.py` `SwingLaunchInput`) that input is an **explicit
human WATCH selection** with reviewed session, clock and daily evidence
packages. It is never derived automatically from data or from a model state.
After admission, candidates therefore need an operator launch review per
symbol. The platform does not generate those reviews by itself, by design.

## Rollback

- Stop daily acquisition: `crontab -l | grep -v '# EGX_TWELVE_DATA_DAILY$' | crontab -`.
- Stored artifacts are immutable evidence and are not deleted. Candidate use
  is controlled only by the registry declaration, so reverting that commit
  removes admission.
