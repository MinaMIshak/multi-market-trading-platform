# Provider architecture, data rights and runbooks

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED. A candidate is not a fill. Source
selection is recorded in `docs/SOURCE_DECISION_MATRIX.md`.

## Layers

```
source adapters ─► registry/admission ─► acquisition (Fetcher / TV helper) ─► raw evidence (sha256, immutable)
   ─► parsing + canonical normalization ─► validation / quarantine ─► session-aware freshness
   ─► cross-source reconciliation ─► context layer (regimes, fusion) ─► ranking (market truth only)
   ─► Paper/Shadow ledger + lifecycles ─► reports ─► runtime snapshot bundle ─► UI/API
```

Market truth and context evidence never mix:

| | Market truth | Context evidence |
|---|---|---|
| EGX | DB-backed validated daily artifacts (`app/data/...`), EGX-RANK-v1 | – |
| US | `app/us_run.py`, with immutable canonical artifacts under `<us-data>/us/canonical/<session>/` and US-RANK-v1 | – |
| Context | – | `app/context/` writes `context-report.json` (markets, rates, FX, gold, Brent, disclosures, fundamentals, geopolitics, cross-market, regimes) |
| Fusion | classification and score unchanged | `app/context/fusion.py` (FUSION-v1) adds a bounded overlay at display time |

## Modules

| Module | Role |
|---|---|
| `app/context/fetch.py` | HTTPS-only fetcher. Per-host spacing (GDELT 6 s, SEC 0.2 s, Yahoo 1 s). Bounded exponential backoff on 429/5xx and network errors, with Retry-After honoured. No retry on other 4xx. Size caps. Content-addressed raw store with corruption detection |
| `app/context/tradingview_series.py` | Generic TradingView daily series with explicit per-instrument specs: expected type, timezone and currency; roll rule for 24-hour markets; completed sessions only; OHLC validation; duplicate dates fail closed |
| `app/context/series.py` | Session-aware freshness (NYSE / EGX / FX / PUBLICATION calendars), point-in-time summaries, reconciliation (AGREE / DISCREPANT / UNVERIFIED), common-date correlations (no forward-fill) |
| `app/context/official.py` | ExchangeRate-API reference, IMF IFS Egypt, BIS policy rates, Fed press releases, EGX disclosures and financial statements, IMF PortWatch, OFAC SDN, GDELT, SEC contact gate |
| `app/context/regime.py` | CONTEXT-v1 regimes and risk flags; cross-market pairs |
| `app/context/run.py` | Orchestrates the context report and refreshes the FRED macro report |
| `app/us/universe.py`, `app/us/nyse_calendar.py`, `app/us/sources.py`, `app/us_run.py` | US master, universe, calendar, admission and pipeline |
| `app/ui/context.py`, `app/ui/us.py` | RESEARCH panels; US TODAY/SWING/PRE-SURGE/PERFORMANCE |

## Data rights and provenance

- Every acquired payload is stored before parsing as
  `<data-root>/context/raw/<source>/<sha256>.<ext>`. The report records the
  URL, the SHA-256, the retrieval time and any HTTP `Last-Modified` / `ETag`.
- Rights labels are recorded per series or section:
  - `US_GOVERNMENT_PUBLIC_DATA_ATTRIBUTION_REQUIRED`: Fed / EIA via FRED.
  - `THIRD_PARTY_COPYRIGHT_CBOE_VIA_FRED_PERSONAL_RESEARCH`: VIX.
  - `NO_CONTRACTUAL_LICENCE_OPERATOR_ACCEPTED`: TradingView, the scanner and
    Yahoo verification.
  - Official publishers are named for IMF, BIS, the Fed, EGX, OFAC and IMF
    PortWatch.
- No contractual licence is claimed for a third-party market-data source.
- A reference rate (ExchangeRate-API USD/EGP, IMF lagged CBE rate) is never
  presented as an official current rate or as a tradable quote. A futures
  reference (UKOIL) is never reconciled against dated spot (EIA Brent); the gap
  is reported.
- Media narrative (GDELT) is shown as narrative only and is never an input to
  regimes, fusion or ranking.

## SEC contact (the only optional user action)

The SEC requires a User-Agent that contains a contact e-mail. Without one,
`data.sec.gov` answers 403. The platform does not invent or borrow a contact.
To enable US filings, fundamentals and SEC listing verification, write one
line containing your contact e-mail into
`/home/egx-agent/er1-autopilot/state/config/sec-contact.txt` (or set
`EGX_SEC_CONTACT`). Until then the SYSTEM and RESEARCH pages show `BLOCKED`.

## Runbooks

**EGX chain** (`tools/egx_nightly.sh`, cron 08:30 Cairo, Sun–Thu):
calendar → TradingView EGX daily → EGX ranking → universe scan → context →
publish (shared `publish.lock`).

**US chain** (`tools/us_nightly.sh`, cron 06:15 Cairo, Tue–Sat):
`app.us_run` (about 30 minutes for 500 names) → context → publish (shared
lock). Both chains pass every report to publish, so neither drops the other's
evidence.

Manual runs, from a release directory:

```
python -m app.us_run --data-root /home/egx-agent/research-data/us-paper-shadow/data \
  --state /home/egx-agent/er1-autopilot/state/us --tv-python <tv-venv>/bin/python
python -m app.context.run --data-root /home/egx-agent/research-data/paper-shadow-operational/data \
  --report /home/egx-agent/er1-autopilot/state/context/context-report.json \
  --macro-report /home/egx-agent/er1-autopilot/state/macro/macro-context.json --tv-python <tv-venv>/bin/python
```

## Recovery and rollback

- **A source fails:** the section shows `UNAVAILABLE` with its code, and other
  sections still publish. No stale value is carried forward.
- **A US artifact conflict:** `CONFLICT_KEPT_EXISTING` is recorded and the
  existing artifact stays. Investigate the raw streams by SHA-256 under
  `<us-data>/context/raw/tradingview_us/`.
- **A bad bundle:** `python tools/publish_runtime_snapshot.py rollback --pointer
  /home/egx-agent/er1-autopilot/state/public-bundle.json`.
- **A bad release:** restart 8001 on the previous release (`docs/PREVIEW_RUNTIME.md`).
  Older releases ignore the newer bundle files (`context-report.json`,
  `us-ranking.json`), because manifest verification hashes every listed file.
- **Disable the US chain:** remove the `# EGX_US_NIGHTLY` crontab line.
