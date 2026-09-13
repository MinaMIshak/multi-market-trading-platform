# ER1C NYSE corporate-action product qualification

Status: **product scope qualified; historical event evidence and US4 admission NO-GO**.

On 2026-09-13, the bounded ER1C probe retained the current official NYSE
corporate-actions product page and version 2.2.6 of the official client
specification. Original bytes, source locators, actual UTC receipt timestamps,
sizes and SHA256 values are stored outside Git under the ER1C research-data root.

The product page says that the NYSE Group package covers more than 60 action types
for NYSE Group equities, including cash and stock dividends, distributions,
splits, new listings, suspensions and delistings. It separately describes a
current-trading-day event summary and programmatic access. The retained client
specification documents the product layout. These facts qualify NYSE as an
authoritative source for records delivered by this product within their exact
scope.

The captured artifacts are documentation, not event files. They contain neither
a complete 2022 action inventory for IBM and TWTR nor explicit negative coverage
for action types absent from that inventory. The public documentation also does
not establish that historical product files can be obtained and retained at zero
cost. No event API, subscription, sample event file or paid service was used.

Consequently, the documentation cannot supply `USCorporateActionCoverage`, prove
historical availability, or satisfy US4. It must not be joined to Tiingo rows as
if it authenticated dividend ex-dates, empty action intervals, the Twitter merger,
or any other event. Current receipt timestamps remain research-build evidence
only.

The offline auditor in
`tools/audit_er1c_nyse_corporate_actions_product.py` verifies both artifact hashes,
the closed package inventory, exact source and purpose, receipt chronology, PDF
identity and the product-page scope anchors. Its output remains fail-closed:
`complete_bounded_us4_action_coverage=NO_GO` and
`canonical_pit_admission=NO_GO`.

Next, search original issuer and SEC filings for dated action facts and continue
looking for a genuinely free complete exchange event archive. Individual notices
may corroborate positive events, but cannot establish complete bounded coverage.
