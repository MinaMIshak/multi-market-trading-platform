# ER1C NYSE corporate-action source qualification

Status: **NO_GO for complete historical action coverage**. This bounded source
qualification does not admit a US4 package, a market observation, or a strategy
result.

The [official NYSE corporate-actions page](https://www.nyse.com/regulation/corporate-actions-market-watch-proxy-compliance)
states that listed issuers must give advance notice and publicly disseminate
information for corporate actions affecting a listed security. Its examples
include name, symbol and CUSIP changes, reverse splits, redomestications,
reorganizations, and tender or exchange offers. It describes a free ticker list
of **upcoming** events published daily, containing issuer, symbol, anticipated
date, action type and status. Event-specific info notices are available through
a Market Data subscription.

This is authoritative evidence of the current NYSE publication policy within
that exact scope. It is not a complete free historical archive, does not prove
the 2022 pilot's event inventory, and cannot establish explicit empty coverage
for any mandatory action category. An issuer notice rule also cannot prove that
all notices were preserved, retrieved, historically available, or mapped to the
correct stable security identity. Subscription-only notices were not acquired
under the zero-budget constraint.

## Retained bytes and offline audit

Package: `/home/egx-agent/research-data/er1c-us-pilot/nyse-corporate-actions-scope-20260913T053527Z`.

- Artifact: `nyse_corporate_actions.html`; HTTP 200; 141,690 bytes.
- Receipt interval: `2026-09-13T05:35:27.952298Z` through
  `2026-09-13T05:35:28.021264Z`.
- Artifact SHA256: `b1068aeffb373d4b25062086c1fd51443d602a72851c7fa243b81654bb52fba1`.
- Manifest SHA256: `176a4ec8075ae06dfe877591e4fe37f8e832a8027f42490fa69471526bc3ab89`.

The offline auditor verifies the closed three-file inventory, manifest and
artifact hashes, exact official locator, HTTP status, UTC receipt ordering,
explicit lack of historical-availability proof, and six publication-scope
anchors. It always reports complete bounded US4 coverage and canonical PIT
admission as `NO_GO`.

```bash
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python \
  tools/audit_er1c_nyse_corporate_actions_scope.py \
  /home/egx-agent/research-data/er1c-us-pilot/nyse-corporate-actions-scope-20260913T053527Z
```

Current receipt time is not historical availability. No candidate, fill,
return, OOS evidence, or readiness promotion was created. The ER1C pilot and
the wider program remain **EXPERIMENTAL / PAPER ONLY**.

## Next action

Do not repeatedly download this current page to seek 2022 completeness. Search
free issuer and regulator archives for original dated notices and ex-date facts,
while preserving that individually proven events still cannot establish complete
bounded action coverage. Historical universe, sessions, identity continuity,
all-action completeness, execution economics and approved PIT reviews remain
unresolved.
