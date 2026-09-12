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

`app.paper.shadow_records` now supplies a strict `shadow-watchlist-v1` document
and `freeze_watchlist` collector boundary. It requires exact UTC generation,
information and independently supplied session/cutoff clocks; canonical market
and MIC pairing; original-artifact hashes, locators, authority and availability;
exact evidence use; explicit stable-identity status; and complete candidate
context. BUY_CANDIDATE records require coherent long entry/stop/targets, passed
liquidity, approved paper risk and a positive hypothetical quantity. WATCH and
AVOID cannot reserve a quantity. Probability and holding window are fixed to
`NOT_YET_VALIDATED`, and confidence remains `EXPERIMENTAL`.

The structured collector validates and deterministically serializes the record,
then delegates to the same immutable freeze primitive. It does not authenticate
the asserted source or session evidence and cannot make a record scoreable by
itself. Every resulting envelope remains **UNADMITTED / NOT SCORED**. An empty
candidate tuple is retained as an explicit frozen watchlist, but needs authentic
upstream candidate-generation evidence before it can support a no-trade result.

## Remaining admission work

`app.paper.shadow_collection` is the operational collection boundary. It accepts
only exact `HistoricalEvidencePackage` objects admitted by the existing PIT
availability and approved-review gate. Every watchlist evidence ID must equal the
package identity and its source locator and artifact SHA256 must bind the raw
receipt. It freezes the structured watchlist and publishes a separate atomic
`shadow-completion-v1` receipt before the cutoff. The receipt remains **NOT
SCORED**; authentication and timely completion are prerequisites, not strategy,
candidate, liquidity, execution or performance validation.

After the cutoff, `record_missed_session` can preserve `MISSED / NOT SCORED` only
for an authenticated session and only when the corresponding watchlist path does
not exist. It cannot run early or overwrite a receipt. A watchlist left behind
without a completion receipt after a failure is not complete and remains
unscored. These local receipts are auditable integrity records, not independent
timestamp attestations.

Target collection dates are EGX 2026-09-13 and US 2026-09-14, as requested;
these dates are not assertions that exchange sessions have been verified. If a
session starts without a valid durably frozen watchlist, record MISSED / NOT
SCORED and proceed to the next eligible session. Never reconstruct earlier picks.
No authentic watchlist has been frozen by this software milestone.

Next add an auditable candidate/trigger/fill/position/exit ledger, conservative
execution admission using the existing simulators, normalized shared paper NAV,
cost/FX-aware reporting and a daily view. Unknown mandatory truth blocks the
affected claim. A preserved candidate alone is never a fill. Daily-bar ambiguity
must not choose favorable stop/target ordering. Historical ER1C acquisition and
admission remain open; forward infrastructure does not establish historical edge,
paper validation or live readiness.

### Collection metadata consistency

Completion revalidates the entire structured watchlist before any publication.
Each reference's `available_at` must equal its bound package's exact availability,
or the inclusive latest endpoint for `BOUNDED_INTERVAL`. The bounded endpoint is
conservative metadata, not a claim of exact publication time. An earlier asserted
time cannot replace the package's proof, even when both precede the cutoff.
Missed-session reasons are runtime restricted to `NO_TIMELY_WATCHLIST` and
`COLLECTION_FAILED`; a type annotation alone does not enforce that restriction.
These checks do not establish semantic session/identity/price truth from arbitrary
reviewed artifacts. Canonical fact admission and execution gates remain required.

### Completion audit boundary

`audit_completed_watchlist` requires the caller's exact structured watchlist and
historical evidence packages, reads the corresponding completion receipt and
frozen envelope, and revalidates their bindings. It checks exact serialized
watchlist bytes, both hashes, record/market/date/cutoffs, package identities and
receipt status. Duplicate receipt keys and unexpected fields fail closed.
Generation must precede or equal freeze receipt, which must precede or equal
completion; completion must be strictly before cutoff and not in the future.
Package receipt/review availability is rechecked at freeze receipt time, not the
later audit clock. Missing completion receipts cannot pass this boundary.

The returned receipt remains **EXPERIMENTAL / PAPER ONLY — NOT SCORED**. The
reader performs no writes, candidate admission, fill inference or NAV update.
Use a trusted local directory without concurrent mutation during audit. Hashes
and local clocks cannot establish independent timestamp attestation or semantic
truth, nor rule out deliberate coordinated rewriting. A process crash after
receipt publication but before its final clock check remains an operational
attestation limitation. Canonical facts and auditable timing controls are still
required before scoring.

### Append-only candidate ledger

`app.paper.shadow_ledger.append_candidate_event` accepts only an exact structured
watchlist whose completion passes the audit boundary again. It publishes one
immutable, content-addressed event per completed watchlist under
`candidate-ledger/`. The event binds the exact candidate records, explicit count,
market/session cutoffs, completion bytes hash, watchlist envelope hash and
structured-document hash. An empty watchlist produces an explicit zero-candidate
event; absence of an event is never interpreted as a no-trade decision.

Candidate IDs never become paths. The event ID and filename are a SHA256 of the
canonical bound content, and atomic create-if-absent publication prevents an
existing event from being replaced. `audit_candidate_event` rejects duplicate or
unexpected JSON fields, future/rollback clocks, altered status or candidate
content, and performs no writes.

Every event remains **EXPERIMENTAL / PAPER ONLY — NOT SCORED** with `NO EXECUTION
INFERENCE`. It is a candidate-preservation boundary only: it does not establish
canonical session, identity, price, liquidity or risk truth beyond what the
upstream records and packages actually prove, and it creates no trigger, fill,
position, P&L or NAV.

### Forward fact admission

`app.paper.shadow_facts` adds the canonical fact boundary required before trigger
evaluation. One `shadow-forward-facts-v1` bundle binds an exact known candidate
event to its open session, stable listing identity, complete corporate-action
coverage through the session, and one or more final `RAW_UNADJUSTED` bars. Bars
must start at the authenticated session open, use gap-free sequence and time
continuity, remain inside the session, carry coherent positive OHLC and
nonnegative volume, and cannot be admitted before their stated availability.

Every fact role references an exact reviewed `HistoricalEvidencePackage`. The
package set must equal the referenced set, pass the existing availability and
build-clock gate, and declare all canonical fields consumed for that role in
`covered_fields`. Each bar's `available_at` must equal its package's exact or
latest bounded availability. An earlier convenient timestamp cannot replace the
evidence boundary. `COMPLETE` action coverage may contain an explicit empty event
tuple. `UNKNOWN` coverage is not admissible.

`append_forward_fact_event` publishes a content-addressed, atomic event binding
the candidate event hash, exact canonical facts and package identities.
`audit_forward_fact_event` rechecks candidate and evidence bindings, clocks,
semantic field coverage, duplicate/unexpected fields and exact event content
without writing. These events remain **EXPERIMENTAL / PAPER ONLY — NOT SCORED**
and say `NO TRIGGER OR FILL INFERENCE`. They do not decide whether an entry
condition fired, resolve price ordering within a bar, create a fill or position,
or update P&L/NAV. Source review remains a trusted attestation rather than
cryptographic proof of source truth. The next milestone is a separate trigger
evaluation event with conservative gap and same-bar ambiguity handling.
