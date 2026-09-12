# ER1C US Free PIT Pilot Predeclaration

Status: frozen before any Tiingo market-data acquisition.

Purpose: validate authentic-data acquisition, evidence admission, stable identity,
corporate-removal handling and PIT plumbing. This is not strategy validation.

Market: NYSE / XNYS
Security type: common stock

Cohort:
- IBM — survivor/control case.
- TWTR — historically listed later-removed merger/delisting case.

Selection rule:
The cohort is selected only for structural coverage: one continuing listing and
one listing removed by a corporate event. Selection is not based on returns,
signals, profitability, volatility or subsequent strategy outcomes.

SEC identity evidence:
- IBM CIK 0000051143; 2022 filings identify IBM common stock on NYSE.
- Twitter CIK 0001418091; 2022 filings identify TWTR common stock on NYSE.
- Twitter's merger completed 2022-10-27 and its filing states trading was
  suspended before the NYSE open on 2022-10-28 for delisting.

Acquisition window:
- warm-up/raw-history start: 2022-04-01
- pilot observation start: 2022-07-01
- last expected TWTR trading date: 2022-10-27
- post-removal evidence horizon end: 2022-11-04

Acquire raw unadjusted daily OHLCV for IBM and TWTR only.
Adjusted values, if supplied, are audit references only.

Do not infer a bar after delisting.
Do not interpret a missing bar as a closed session or removal without evidence.
Do not forward-fill identity, membership, prices, actions or session state.

The pilot succeeds only as an acquisition/admission test.
It does not establish a complete historical NYSE universe, statistical edge,
survivorship-safe full-market research, paper readiness or live readiness.
