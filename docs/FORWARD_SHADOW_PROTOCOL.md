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

`app.paper.shadow_records` now supplies a strict `shadow-watchlist-v2` document
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
evaluation. One `shadow-forward-facts-v2` bundle binds an exact known candidate
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


Issue-specific tradability is mandatory independently of the exchange calendar,
corporate actions and vendor price rows. `ForwardTradingStatusFact` requires an
exact stable instrument and listing MIC, explicit UTC coverage bounds spanning
all supplied bar intervals, and affirmative `TRADABLE` status backed by a reviewed
package covering every trading-status field. Missing, UNKNOWN, HALTED or SUSPENDED
status fails admission; neither positive volume nor a vendor row overrides it.
Partial-session status cannot authorize bars beyond its coverage. An absence of
halt notices alone does not establish affirmative complete coverage: source
review must attest the issue-specific scope and reconcile conflicting evidence.
Original rejected source bytes remain retained outside the repository.

The fact bundle and event schemas are now v2. Prior v1 records are not upgraded
or inferred to contain tradability evidence; reaccreditation requires authentic
status evidence and a new event. This change does not infer any trigger or fill.

### Trigger evaluation

`app.paper.shadow_triggers` consumes only an audited v2 forward-fact event and the
exact completed watchlist and evidence packages that produced it. It evaluates
the predeclared `LONG_ENTRY_ZONE_TOUCH_V1` rule in opening-origin final bars and
appends a separate content-addressed `shadow-trigger-event-v1`. The event remains
**EXPERIMENTAL / PAPER ONLY — NOT SCORED** and explicitly creates no fill or
position. Earlier v1 watchlists lack a machine-readable entry rule and cannot be
upgraded or used for trigger evaluation.

An open at or below the stop before entry is `INVALIDATED_OPEN_GAP`; an open at
or above target 1 before entry is `TARGET_PASSED_OPEN_GAP`. Neither can become a
later trigger. A bar whose range does not touch the entry zone is
`NOT_TRIGGERED`. A zone touch records only a reference price (open when inside
the zone, otherwise the first zone boundary implied by the open), never an exact
intrabar time or executable fill. If that bar also spans both stop and target 1,
the event is `TRIGGERED_AMBIGUOUS_BAR`; the same applies when an open below the
entry zone leaves stop-before-entry versus stop-after-entry unknowable. No
favorable path ordering is chosen, and later bars cannot resolve that ambiguity.
The symmetric case is also ambiguous: when the bar opens above the entry zone
and reaches target 1, its range cannot establish whether the target preceded or
followed entry. A high exactly equal to target 1 counts as a touch. An open
inside the entry zone does not create this particular ambiguity; it still does
not prove an executable fill.

Append and audit recompute the deterministic evaluation, bind the exact upstream
fact-event ID and bytes hash, enforce fact availability and local UTC ordering,
and reject duplicate/unexpected fields or changed content. These mechanics do
require the trigger recording time to be at or after the upstream fact-event
recording time, during both publication and audit. Bar availability alone cannot
authorize a trigger event backdated before the fact event it consumes. They do
not authenticate arbitrary source semantics or establish queues, participation,
spread, slippage, costs, fills, positions, P&L or strategy edge. Simulated-fill
admission is the separate downstream boundary described below.

### Simulated entry-fill admission

`app.paper.shadow_fills` consumes the exact audited trigger chain and accepts only
the `TRIGGERED` status. `TRIGGERED_AMBIGUOUS_BAR`, non-trigger, invalidation and
target-gap outcomes cannot produce a fill. The boundary reuses the authenticated
trigger bar and candidate's predeclared full paper quantity; it does not invent a
queue, partial fill, tick path or exact intrabar timestamp.

Every `shadow-fill-policy-v1` supplies exact Decimal entry slippage, variable and
fixed per-side costs, maximum volume participation, market and currency. Separate
reviewed evidence-package references must cover each assumption. The exact package
set is re-admitted at the watchlist information cutoff, so evidence acquired,
reviewed or available only after candidate selection cannot tune the simulated
economics. US policies use USD and EGX policies use EGP. These are explicit
evidenced assumptions, not measured spread or impact unless their source packages
actually establish that stronger scope.

Before a trigger may become a fill, `freeze_fill_policy_selection` publishes one
immutable `shadow-fill-policy-selection-v1` receipt for the exact completed
watchlist. The receipt binds the completion and watchlist hashes, market, full
evidenced fill policy and exact evidence-package identities. It must be published
before the authenticated session opens. All policy variants for that watchlist
compete for one atomic path, so observed facts cannot be used to choose among
slippage, cost or participation assumptions. Duplicate fields, alteration,
future clocks, post-open selection and a caller-supplied alternate policy fail
closed. Every fill append and audit re-audits the selection and requires it to
precede trigger publication. This freezes operational assumptions; their
empirical accuracy remains bounded by the admitted evidence.

Capacity is `floor(authenticated trigger-bar volume * participation fraction)`.
If the declared quantity exceeds it, the event records `NO FILL: INSUFFICIENT
CAPACITY`; there is no partial fill. An admitted long fill applies buy-side
slippage adversely to the trigger reference price, rejects a slipped price outside
the candidate's stop/target geometry, and records exact quantity, raw and slipped
price, bar interval, fact availability, notional and entry-side cost.

`append_fill_event` publishes one atomic content-addressed
`shadow-fill-event-v1`, binding the upstream trigger bytes hash, policy, package
identities and optional fill. Publication cannot precede the trigger event.
`audit_fill_event` recomputes the full chain and rejects duplicate fields,
tampering, future clocks and backdating. Every event remains **EXPERIMENTAL /
PAPER ONLY — NOT SCORED**. It explicitly creates no position event, exit, P&L or
NAV. Position state and conservative exit processing are later boundaries.

### Position-open provenance

`app.paper.shadow_positions` consumes only a durable entry-fill event that passes
`audit_fill_event` and its complete upstream watchlist/fact/trigger evidence chain.
An insufficient-capacity event cannot open a position. Atomic create-if-absent
publication under `position-open-events/`, keyed by a hash of market, frozen
watchlist record ID and candidate ID, prevents alternate fact or fill-policy
evaluations from opening multiple positions for that frozen candidate in the
same ledger. The v2 event binds this key and watchlist ID. Atomic create-if-absent
publication also arbitrates concurrent writers; a losing evaluation cannot
replace the winner, and audit rejects a different fill chain at the same key.
This is a uniqueness constraint, not permission to select a favorable fill:
operational policy selection must still be frozen before evaluation.
The content-addressed event binds the exact fill bytes hash, stable identity,
quantity, native currency, entry interval/availability, slipped price, notional,
entry cost and original frozen stop/targets. It preserves the bar interval; it
does not invent an exact intrabar fill timestamp.

Append and audit enforce local UTC ordering after the fill and reject duplicate
JSON fields, altered entries/exits, unexpected fields, future clocks and
backdating. Audit recomputes the upstream chain without writing. The same trusted
local storage and clock-attestation limitations described above apply.

`POSITION_OPENED` means **SIMULATED OPEN AT ENTRY**, not a claim of current
holdings after later bars. All output remains **EXPERIMENTAL / PAPER ONLY — NOT
SCORED**, with **SHARED CAPITAL NOT ALLOCATED** and **NO MARK, EXIT, P&L OR NAV**.
The boundary is a provenance prerequisite, not portfolio reconciliation across
different watchlists, sessions or ledgers. Legacy v1 fill-keyed events are not
admitted by the v2 auditor; existing ledgers require explicit reviewed migration
before reuse, without deleting or rewriting evidence. Shared capital,
candidate-level allocation, conservative exits (including entry-bar
stop/target uncertainty), marks, FX and performance require separate admission.
No authentic position or performance result was created by this milestone.

### Conservative exit provenance

`app.paper.shadow_exits` re-audits the durable position-open event and its entire
watchlist, fact, trigger and fill chain before evaluating an exit. A separately
predeclared `shadow-exit-policy-v1` requires exact Decimal stop and target
slippage plus variable and fixed exit-side costs. The referenced evidence
packages must exactly cover those fields and be safely known by the frozen
information cutoff.

For a long paper position, an open below the stop exits at that worse open; an
open above target 1 remains capped at target 1. Other single-boundary touches use
the frozen stop or target. Sell slippage is always adverse. If one bar touches
both stop and target and their order is unknowable, the result is `UNKNOWN` and
no exit fill is invented. The evaluated bar interval and availability are
preserved without an invented intrabar timestamp.

One atomic event per exact position and authenticated fact snapshot is stored
under `exit-events/`. A later fact snapshot may append another immutable
evaluation for a position that remained open; replaying the same snapshot cannot
overwrite or duplicate it. The later snapshot must preserve every originally
admitted bar exactly and may only append bars. Revision or truncation fails
closed even when the changed snapshot independently passes fact admission;
later receipt does not authorize rewriting the position's observed history.
Audit binds the position bytes hash, exact
forward-fact event ID and bytes hash, exact policy and evidence package
identities, and rejects
duplicate or additional JSON fields, backdating, future clocks, tampering and
upstream corruption. An `OPEN` result states only that no exit was observed in
the supplied admitted bars; it is not a current mark. Every result remains
**EXPERIMENTAL / PAPER ONLY — NOT SCORED**, with **SHARED CAPITAL NOT ALLOCATED**
and **NO P&L OR NAV**. Multi-session continuation, candidate-level
deduplication, allocation, marks, P&L, FX and portfolio NAV remain separate
boundaries. No authentic exit or performance result was created by this
milestone.

Legacy `shadow-exit-event-v1` position-only filenames are not admitted by the
v2 auditor. Preserve them unchanged; any migration requires explicit review and
must retain their original bytes. A collection record ID is shared by successive
fact snapshots and therefore cannot identify an evaluation snapshot.

### Shared normalized portfolio policy

`app.paper.shadow_portfolio` declares one immutable policy at the shared ledger
root (`portfolio-policy.json`). Performance NAV is normalized to 100, independent
of personal wealth, while an explicit positive research-capital amount supplies
the monetary denominator in the declared base currency. EGX and US capital caps
are fractions of that **same** total;
their sum plus the minimum cash fraction cannot exceed one. Position and aggregate
stop-risk caps are explicit fractions of the total, with internally consistent
bounds. Exact rational checks avoid Decimal-context rounding admitting an excess.
These caps are experimental research choices, not empirically calibrated limits
or guarantees against gap losses.

Atomic publication permits only one policy per ledger root, regardless of market
or session. Freeze requires the local clock strictly before the declared effective
time; audit binds all fields and rejects altered, duplicate or future receipts.
This effective time does not authenticate an exchange session. The eventual
reservation gate must also require effectiveness and freezing by each admitted
watchlist's information cutoff. Local storage/clock trust limitations still apply.
Separate ledger roots must never be aggregated as one funded portfolio.

This boundary is **EXPERIMENTAL / PAPER ONLY — POLICY ONLY / NOT ALLOCATED**.
It neither changes existing position provenance nor creates capital reservations.
Before operational portfolio allocation, implement atomic shared reservations
including costs and existing exposure across candidates, sessions and markets,
binding this policy and the entire authenticated fill chain. Each reservation must
respect its own sleeve as well as aggregate and position caps. Foreign-currency
notional, costs and risk require admissible point-in-time FX conversion; without
it that allocation is NO-GO. No equal-currency assumption or independent full-NAV
market sleeves are permitted. No policy has been frozen for authentic operations
by this software milestone; performance and NAV remain unavailable.

### Shared capital reservation

`app.paper.shadow_allocations` reserves normalized paper capital only after it
reaudits the frozen watchlist, authenticated facts, fill-policy selection,
simulated fill, position-open event and single frozen portfolio policy. The
policy must be frozen and effective by the watchlist information cutoff. An OS
file lock serializes reservations across candidates, sessions and markets under
one ledger root; every receipt binds preceding reservation IDs and exact totals.

Reserved capital is fill notional plus entry cost. Stop risk includes the
fill-to-stop loss, entry cost and estimated policy cost for an exit at the frozen
initial stop. Exact rational comparisons enforce per-position capital and risk,
market sleeve, minimum cash and aggregate risk caps against the declared monetary
research capital. Normalized NAV remains the separate performance index base.
The receipt preserves no mark, return, P&L or performance claim. A fill currency
different from the portfolio base fails closed because no admitted PIT FX
conversion boundary exists. Reservations are simulated ledger events, never
broker orders.

Before admitting further exposure, the reservation reader checks every stored
receipt's fixed paper-only semantics, reference formats, candidate filename,
finite nonnegative amounts, cumulative totals, unique position IDs and monotonic
nonfuture receipt clocks. Recomputing a hash does not bypass these checks. New
publication must also follow the preceding reservation clock. Totals are checked
using the same isolated Decimal precision as publication. These are local ledger
integrity checks, not independent timestamp attestation or authentication of all
prior source packages. The target reservation still requires full upstream audit;
revalidation of all predecessor source chains and authenticated exit settlement
remain necessary before operational use. Closed positions continue to consume
capital until that settlement boundary is implemented.

### Shared capital settlement

`app.paper.shadow_allocations` releases a reservation only from a durable exit
event whose fully reaudited input chain evaluates to `CLOSED`. `OPEN` and
`UNKNOWN` outcomes cannot settle. The append-only settlement binds the original
reservation, preserved closed-exit bytes, policy, market and native currency. It
records capital and stop-risk released together with exit notional, exit cost and
net exit proceeds. These are cash-flow facts labelled **EXPERIMENTAL / PAPER
ONLY**; they do not create NAV, aggregate P&L or a performance claim.

Later reservations validate every local settlement and its referenced closed
exit before excluding the settled exposure. Realized losses reduce the remaining
aggregate and market-sleeve capacity. Paper gains do not enlarge either cap, so
unvalidated profits cannot compound research exposure. The historical
reservation chain and cumulative receipt totals remain append-only and are never
rewritten. Risk is released only at the same authenticated settlement boundary.

The local reader verifies hashes, exact finite nonnegative cash values, receipt
semantics, filenames, unique reservation settlement, policy/market/currency
binding and nonfuture clock ordering. This does not independently attest local
clocks or reauthenticate every historical source package without those original
inputs. Portfolio NAV, marks, combined-market performance and PIT FX remain
separate unavailable boundaries.

### Excess-loss halt

Before any new reservation, a settled native loss (released entry capital minus
net exit proceeds, including both sides' costs) is compared exactly with that
reservation's released stop-risk estimate. A strictly greater loss halts all new
reservations in the shared ledger, across both sleeves. Equality does not trigger
this gate. Gains elsewhere cannot offset a breach. Existing settlements and
historical reservation audits remain available; no receipt is rewritten.

This mandatory conservative rule has no automatic reset or override. Do not open
a new ledger to evade a halt. An explicit reviewed recovery protocol would be a
separate milestone. This detects settled excess loss; it does not bound gap losses,
monitor unrealized losses or make multi-session execution operational. The initial
stop-risk estimate still omits exit slippage; even an ordinary stop exit may halt
reuse. All outputs remain **EXPERIMENTAL / PAPER ONLY**.

### Read-only collection view

`app.paper.shadow_report.watchlist_collection_view(directory, watchlist,
evidence_packages)` returns a JSON-serializable view after re-auditing the durable
candidate event, completion receipt, frozen document and evidence packages.
It preserves every candidate, including WATCH/AVOID, its entry zone, stop,
targets, thesis, context, experimental confidence, unvalidated horizon and
probability, declared liquidity/risk fields, and source references. Declared
paper quantity is not an allocation or simulated fill. Hash references bind the
view to its upstream records. The reader writes no files.

`missed_collection_view(directory, record_id=..., session=...,
session_package=...)` instead requires an existing audited missed receipt.
It returns **MISSED / NOT SCORED**, with unknown candidate count/list; it never
reconstructs predictions. A frozen empty watchlist has count zero and an empty
list. Missing receipts or failed audits raise errors rather than producing a
plausible report.

This is a one-record collection view, not a complete daily portfolio dashboard.
Market status, downstream execution, open/closed positions, NAV, returns,
drawdown, hit rate, expectancy and market attribution remain explicitly
unevaluated, with unavailable numerical metrics represented by null, never zero.
It does not infer present market state from a previously declared session or
infer cash-only performance from an empty candidate list. Full daily aggregation,
audited marks and cost/FX-aware performance remain outstanding.
No authentic collection or empirical result was created by this milestone.

`position_open_view(...)` re-audits the complete frozen watchlist, fact, trigger,
fill-policy and fill chain before exposing the durable entry record, initial stop
and targets. It reports only **SIMULATED OPEN AT ENTRY**. Current position status
remains unknown because the view does not assume that no later exit exists.

`exit_evaluation_view(...)` additionally re-audits the position and conservative
exit event. It preserves the exact `OPEN`, `CLOSED` or `UNKNOWN` evaluation and
the bar sequence through which it was evaluated. `OPEN` means only that the
supplied admitted bars contained no observed exit. For an `OPEN` result, the view
reports the final admitted bar close as an authenticated as-of mark, its interval
end and availability time, and the exact gross market value at that price. It
also reports gross unrealized P&L as gross marked value minus audited entry
notional, in the position's native currency, and gross unrealized return over
that entry notional. Entry fill slippage is embedded in that notional; fees and
hypothetical liquidation slippage are excluded. This
as-of valuation is not an executable exit, realized return or portfolio NAV.
Open and closed trade views also preserve the original stop, targets and
unvalidated holding-window declaration. Their observed holding duration is the
exact elapsed time between authenticated entry and observation/exit **bar
ends**. It is explicitly a bounded observation window because the fill boundary
does not invent an intrabar timestamp; it must not be presented as exact time in
position.
Net liquidation unrealized P&L remains unknown
until applicable liquidation slippage and cost are authenticated. For a `CLOSED` result only,
the view re-audits the entry chain again and reports one native-currency closed
trade with exact gross P&L, net P&L after preserved entry and exit costs, and net
return on entry notional plus entry cost. Gross return uses entry notional alone;
it excludes entry and exit costs consistently with gross P&L. `UNKNOWN` results expose no P&L;
`OPEN` results expose only gross marked unrealized P&L. This single-trade arithmetic
is not portfolio performance: neither view
infers a current mark, aggregates positions, converts FX or updates NAV, and all
aggregate performance fields remain explicitly not evaluated/null. `UNKNOWN`
exit evaluations expose no mark because the position state itself is unresolved.
Both readers are read-only and fail closed when any durable event or upstream
evidence binding is missing or altered.

`capital_settlement_view(...)` re-audits the complete portfolio policy,
reservation, conservative closed exit and settlement chain. It reports the
released capital and risk, exit notional, exit cost and net exit proceeds in the
position's authenticated native currency. These are settled cash-flow facts for
one trade. They do not establish portfolio NAV, return, attribution or reusable
cross-currency capital. All aggregate performance fields remain null, and the
reader fails closed if the settlement receipt or any upstream binding is absent
or altered.

`entry_fill_view(...)` re-audits the frozen fill-policy selection, trigger and
entry-fill event, exposing either the preserved simulated entry fill or
**NO FILL: INSUFFICIENT CAPACITY** with a null fill. It preserves the policy,
receipt timestamp and trigger/fill evidence references. Missing or tampered
receipts fail closed. This read-only view does not create a position, infer
current position state, or count a rejected entry as a zero-return trade. An
untriggered candidate is outside this fill-event boundary; it must not be
relabeled as capacity-rejected. Position, exit and aggregate performance remain
unevaluated. Every result remains **EXPERIMENTAL / PAPER ONLY**, **NOT SCORED**.

`trigger_evaluation_view(...)` covers the earlier audited trigger boundary,
including `NOT_TRIGGERED`, pre-entry invalidation or target gaps, and
`TRIGGERED_AMBIGUOUS_BAR`. It re-audits the exact forward-fact and trigger event
chain, preserves the evaluation boundary, reason, optional trigger reference and
receipt timestamp, and exposes the bound fact-event references. It never turns
an untriggered or ambiguous observation into a fill, position, no-fill return or
score. Missing or altered receipts fail closed; position, exit and aggregate
performance remain unevaluated.

Before entry, a bar that never intersects the entry zone still retires the
candidate if its low touches or crosses the stop (`INVALIDATED_BEFORE_ENTRY`),
or its high touches or crosses the first target (`TARGET_PASSED_BEFORE_ENTRY`).
These are terminal observations within the supplied forward bar sequence: a
later entry-zone touch cannot revive that candidate. Exact boundary touches
count. A bar touching both the entry zone and a relevant boundary continues to
use the existing conservative path-order rules. These retirement observations
remain **EXPERIMENTAL / PAPER ONLY**, **NOT SCORED**, with no simulated fill,
position or return. This rule is covered by artificial regression fixtures;
it does not supply authentic session or candidate evidence.

### Exit participation admission

`shadow-exit-policy-v2` requires an explicit exact-decimal participation fraction
in `(0, 1]` and a matching pre-cutoff evidence package covering
`max_volume_participation_pct`. Old v1 policies lack this mandatory input and
cannot be silently upgraded or used to re-admit an exit. Existing immutable
receipts must be retained; this change does not rewrite earlier records.

Before closing a whole position at a stop, target or gap, the evaluator limits
quantity to the integer floor of observed exit-bar volume times the declared
fraction. If entry and exit share a bar, both quantities consume that bound.
Insufficient capacity returns **UNKNOWN / INSUFFICIENT_EXIT_CAPACITY**, with no
exit fill, realized P&L or capital settlement. The evaluator stops at that bar;
a later liquid bar cannot retroactively supply its missing capacity. Partial
fills and delayed liquidation remain unsupported. Existing stop/target ordering
ambiguity still fails closed.

This is a necessary upper-bound check only: total bar volume does not establish
available volume at a particular price or within a particular intrabar interval.
Passing it does not validate executable liquidity. All results remain
**EXPERIMENTAL / PAPER ONLY**, **NOT SCORED**. Tests use artificial software
fixtures; no authentic candidate, fill or empirical result was created.
