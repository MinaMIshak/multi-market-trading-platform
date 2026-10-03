# Macro and cross-asset context

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED. This is context only. No macro value
creates, upgrades or vetoes a candidate.

## Sources (admitted)

| Series | What | Publisher | Stale after |
|---|---|---|---|
| DFF | US effective federal funds rate | Federal Reserve Board (H.15) | 5 days |
| DGS2 / DGS10 | US Treasury 2y / 10y constant-maturity yield | Federal Reserve Board (H.15) | 5 days |
| DTWEXBGS | Nominal broad US dollar index | Federal Reserve Board (H.10, weekly release) | 10 days |
| DEXUSEU | USD per EUR | Federal Reserve Board (H.10, weekly release) | 10 days |
| DCOILBRENTEU | Brent crude spot | U.S. Energy Information Administration | 10 days |

How the data is delivered:
- **Endpoint:** the FRED public CSV endpoint
  (`fred.stlouisfed.org/graph/fredgraph.csv`), with no account, key or payment.
- **Rights:** these are published U.S. government statistics. The report labels
  them `US_GOVERNMENT_PUBLIC_DATA_ATTRIBUTION_REQUIRED` and names the publisher
  on every row.
- **Delivery provenance:** each row also records the URL, the SHA-256 of the
  raw response and the retrieval time.

## Rules (`app/research/macro.py`)

- **Raw storage:** raw bytes are stored before parsing, content-addressed under
  `<data-root>/macro/raw/<series>/<sha256>.csv`, and never overwritten
  (corruption is detected).
- **Parsing:** strict. The header must name the series, dates must be strictly
  ascending, and values must be finite decimals. A blank or `.` value is
  skipped and counted, never filled.
- **Point in time:** observations dated after `as_of` are excluded and counted.
  FRED can revise history, so `retrieved_at` is the known-at time and these
  reports are not backtest inputs.
- **Changes:** Δ 1 obs and Δ 20 obs are shown in percentage points for rates
  and in % for the others.
- **Freshness:** CURRENT when the latest observation is within the series'
  publication lag; STALE otherwise.
- **Derived metric:** 2s10s (10y − 2y, in bp) only when both are dated the same
  day; otherwise UNKNOWN with the reason.
- **Failures:** a failed series is UNAVAILABLE with its cause. Nothing is reused
  from earlier runs.

## Operation

- **Nightly:** step `macro` in `tools/egx_nightly.sh` (before `publish`):
  `python -m app.research.macro_fetch --data-root <DATA> --report <STATE>/macro/macro-context.json`.
  A failure is logged and never blocks the EGX steps.
- **Runtime state:** input `macro` (`EGX_MACRO_REPORT_PATH`, bundle file
  `macro-context.json`), passed with `--macro-report` to the snapshot and
  publish tools.
- **Display:** the RESEARCH page shows the panel. `/api/system` shows `macro`
  as a summary.

## Not yet sourced (shown on the page)

- **Gold spot:** LBMA prices are licensed, and FRED no longer carries them.
- **USD/EGP and the CBE policy rate:** the Central Bank of Egypt's terms have
  not been reviewed.
- **News and catalysts, geopolitical intelligence, fundamentals:** no admitted
  source.

Each item needs a lawful free source with reviewed terms before it is admitted.
