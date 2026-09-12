# Forward shadow collection — EXPERIMENTAL / PAPER ONLY

The first collection prerequisite is `app.paper.shadow_freeze.freeze_document`.
It preserves exact decision-document bytes, SHA256, size, market, record ID,
asserted information and decision cutoffs, and actual local UTC receipt time.
The caller cannot supply a receipt timestamp. Publication uses a same-filesystem
atomic create-if-absent link after file fsync, followed by directory fsync. A
duplicate record ID cannot replace the original, including concurrent writers.
Cutoff equality is late. A cutoff crossed during writing/publication or a clock
rollback below receipt rejects publication. Failed publication cleans up its own
artifact; it never removes an existing competing record.

Use only a trusted directory under `/home/egx-agent/research-data` for authentic
documents. Do not put source bytes in Git. This module neither fetches data nor
connects to brokers. It is additive to existing M6/US paper replay and changes no
historical evidence or execution admission rules.

Every envelope is **UNADMITTED / NOT SCORED**. Preservation is not candidate,
calendar, PIT, risk or execution approval. The opaque document may preserve
rejected candidates and collection failures as well as proposed watchlists.
No empty collection should be interpreted as a successful no-trade strategy.
`audit_document` verifies integrity and recorded receipt ordering only. Receipt
is not a historical source publication time or an independently attested freeze
timestamp. Local hashes do not protect against a privileged actor rewriting the
document and hash together. A complete publication receipt and auditable timing
controls remain necessary before automatic scoreability is enabled. A process
crash between linking and the final clock check may leave an unadmitted artifact;
its existence alone never proves successful completion before the cutoff.

## Next admission milestone

Add a validated recommendation/session record and collector command. Each
candidate must retain market, stable identity when known, symbol, generated and
frozen clocks, information cutoff, BUY_CANDIDATE/WATCH/AVOID, thesis/context,
technical setup, entry condition/zone, stop, targets, holding window,
experimental confidence, liquidity checks, paper-risk size and source bindings.
Unsupported probabilities and holding-time estimates remain UNKNOWN / NOT YET
VALIDATED. Verify evidence availability before the information cutoff; prove
session cutoffs independently. A caller-supplied session clock is insufficient.

Target collection dates are EGX 2026-09-13 and US 2026-09-14, as requested;
these dates are not assertions that exchange sessions have been verified. If a
session starts without a valid durably frozen watchlist, record MISSED / NOT
SCORED and proceed to the next eligible session. Never reconstruct earlier picks.
No authentic watchlist has been frozen by this software milestone.

Then add an auditable candidate/trigger/fill/position/exit ledger, conservative
execution admission using the existing simulators, normalized shared paper NAV,
cost/FX-aware reporting and a daily view. Unknown mandatory truth blocks the
affected claim. A preserved candidate alone is never a fill. Daily-bar ambiguity
must not choose favorable stop/target ordering. Historical ER1C acquisition and
admission remain open; forward infrastructure does not establish historical edge,
paper validation or live readiness.
