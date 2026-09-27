# Observed receipt reporting

The product API and all product sections expose `coverage[market].status_counts`
for available operational observations. Counts partition distinct observed symbols
into WATCH, READY_NO_SIGNAL, NOT_READY, DATA_STALE, EVIDENCE_BLOCKED and UNKNOWN.
These are receipt-reader results, not completed scanner runs or universe coverage.
WATCH remains unsized and never counts as a fill or candidate.

Unavailable readers have null counts; an available empty reader has zero counts.
US remains disconnected with null counts. EGX rows cannot populate US. Duplicate
symbol observations count once as EVIDENCE_BLOCKED, unknown statuses count as
UNKNOWN, and missing symbol identity makes the summary unknown. The existing
receipt reader still determines integrity and expiry before aggregation.

Universe, data-ready, scanned and candidate fields remain null. No totals across
markets, coverage percentages, provider-health claims or performance are inferred.
Temporary SQLite tests are artificial software fixtures, not market evidence.

The preserved external-evidence acquisition manifest contains no admitted source
package for a free daily transport. The configured acquisition factory remains
closed. This reporting capability does not change provider admission or licensing.
