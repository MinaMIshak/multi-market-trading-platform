# US7C-B — Historical US execution replay binding

US7C-B is an additive, offline bridge from US7A planning/risk and admitted
US1/US2/US4/US7B evidence to shared M6.1 replay and M7.1 performance. It changes
no shared simulator, performance analyzer, research-validation semantics,
storage schema, operational flow, or broker integration.

## Public API

Contracts in `app.us.paper_replay_models`:

- `USPaperReplayRequest`: original signal, terms, plan, risk policy, risk
  snapshot/admission, explicit execution config, execution listing/session/action
  histories, ordered admitted intraday sessions, explicit coverage checkpoints,
  execution evidence cutoff, and local research build clock.
- `USReplayCheckpoint`: a local `market_date` and exact UTC
  `path_complete_through_at` on that date.
- `USReplayBarProvenance`: one mapped bar's source/provider/row/evidence lineage.
- `USReplayProvenance`: request identity, consumed checkpoint/fact/admission/proof
  identities, per-bar lineage, checked action dates/IDs, and action/dividend policy.
- `USPaperReplayResult`: either `REPLAYED` with shared input/result and provenance,
  or `INCOMPATIBLE` with a specific rejection reason and no shared execution
  artifacts. Research callers must retain incompatibilities as explicit exclusions.

Functions in `app.us.paper_replay`:

- `derive_us_replay_session_id(session)`
- `replay_us_research_trade(request)`
- `build_us_paper_replay_input(request)` — invokes the same action-gated replay;
  returns only its accepted input, raising on incompatibility.
- `build_us_replay_performance_observation(request, result)` — independently
  revalidates/replays the US binding before constructing a shared observation.

No automatic history acquisition, hidden execution settings, or wall-clock
checkpoint selection is provided.

## Original decision binding

The original US6 signal is canonical upstream research evidence, not reconstructed
from execution bars. US7C-B recomputes `build_us_research_trade_plan(signal, terms)`
and `admit_us_research_risk(plan, policy, snapshot, decision_at, trade_state)` and
requires exact structural agreement with the supplied plan/admission.

This binds stable instrument UUID, historical symbol, MIC, plan and decision UUIDs,
planning-rule version, strategy/config identity, snapshot identity, regime,
quantity/caps, approved risk, and policy identity. The canonical source signal and
snapshot retain their upstream attestation limits; this adapter does not authenticate
external sources or rerun the original strategy on later evidence.

Shared TradePlan and RiskDecision are materialized using existing US7A functions.
Admission time is exactly the risk decision time. The original created-at and
entry deadline remain unchanged. M6.1's stricter geometry is mandatory:
`stop < entry_low <= entry_high < target_1`.

An expired US7A risk BLOCK cannot be backdated to satisfy M6.1. Such an input
returns `ADMISSION_OUTSIDE_PLAN_VALIDITY`. A representable BLOCK produces shared
REJECTED with no position/fill/metrics; it has no pending/open action exposure.

## Evidence, calendar, and complete sessions

Execution listing/session/action histories are re-admitted from their canonical
facts. Their decision horizons must equal `evidence_cutoff_at`, not the original
strategy decision. Build clocks must not exceed the replay build clock.

Every supplied US7B admission is re-admitted and compared in full, including its
flattened bars. Admission identity alone does not protect that separate dataclass
field. Structural checks distinguish bool/int, float/Decimal, Decimal scale,
noncanonical timestamp zones, extra fields, and nested model substitutions.

US2 must explicitly cover every calendar date from admission through the last
requested checkpoint. No weekday, holiday, regular-hours, or DST-session inference
occurs. REGULAR and EARLY_CLOSE become TRADING with exact US2 boundaries. CLOSED
becomes CLOSED with empty bars and no session identity/times/granularity.

Every reached trading date requires complete replay-compatible intraday evidence.
M1 maps to 60 seconds; M5 maps to 300 seconds. FULL_SESSION is re-certified against
US2. A BOUNDED_WINDOW can pass only when its unchanged facts independently pass
FULL_SESSION admission, including sequence 1 and complete open-to-close coverage.
The original admission identity and full-session proof identity are both retained.
A partial window is never renumbered, repaired, filled, or silently promoted.

## Checkpoints and availability

Checkpoints are explicit, strictly increasing, unique local dates. They may be
sparse; the underlying calendar and required trading sessions may not be sparse.
All intervening explicit calendar dates are traversed and action-gated before a
checkpoint is evaluated. This supports bars whose availability spans sessions,
including equal availability timestamps across multiple sessions.

At each checkpoint, the entire accumulated prefix is validated by M6.1. Every
included bar must be available by that checkpoint, all sessions must be complete,
and availability must be globally nondecreasing, including across CLOSED dates.
An invalid early checkpoint is rejected; it is not silently skipped or advanced.

When a checkpoint proves COMPLETED, NO_FILL, or REJECTED, traversal stops. Later
action dates do not change that shared terminal result. Unconsumed future sessions
need not be supplied. Any supplied objects still undergo canonical validation.
If no valid earlier checkpoint proves termination, an intervening unsupported
corporate action cannot be ignored based on a hypothetical earlier exit.

Different requested evidence changes audit/request identity even when the
accepted shared terminal input/result is unchanged. Local build time alone does
not change semantic identity.

## Exact bar and provenance mapping

UUID becomes its canonical string; canonical historical symbol becomes `symbol`;
calendar MIC becomes `venue_id`. OHLCV, optional traded value, market date, session
sequence, interval timestamps, and per-bar availability are copied exactly.
No conversion to float occurs in this adapter. Existing US6 indicator floats are
only retained as original signal fields; they are never execution prices.

`source_id = source_sha256`; `provenance_id` is unchanged. Per-bar lineage also
retains source provider, provider symbol/key, source row number, segment-fact ID,
and evidence-package ID. Different artifacts within one session keep their own
sources. The adapter never claims that the first artifact represents the session.

Session identity is a versioned SHA-256 of canonical JSON containing US market,
MIC, local date, timezone, state, and explicit open/close timestamps. It excludes
instrument/ticker, granularity, artifact source, and build time. Dates and venues
therefore cannot collide semantically. Request identity binds all semantic inputs,
ordered upstream evidence identities, execution config, checkpoints, cutoff, and
action-policy version. Result identity additionally binds exact mapped input,
result, lineage, consumed prefixes, and checked actions. No new random IDs are used.

## Corporate actions

Policy: `us-replay-actions-date-inclusive-v1`.

US4 must provide complete inclusive coverage from plan signal date through the
requested path, including explicit negative evidence. The adapter gates the
signal-date-to-admission bridge, pending entry, open holding periods, and CLOSED
dates. It never infers an action from a gap, ticker change, or adjusted price.

SPLIT, STOCK_DIVIDEND, MERGER, SPINOFF, SYMBOL_CHANGE, DELISTING, RIGHTS, and OTHER
produce explicit `UNSUPPORTED_CORPORATE_ACTION`, retaining triggering event IDs.
No quantity, stop/target, entry zone, ticker, merger consideration, spin-off value,
rights value, or delisting fill is transformed or fabricated.

US4 has effective dates but no intraday effective timestamps. Boundaries are
inclusive: an unsupported event on a prospective exit's date is rejected before
processing that date. A later-date event after a checkpoint-proven exit is ignored
for execution. Pending terms are gated as strictly as open positions because a
split invalidates their raw-price and share-quantity basis too.

CASH_DIVIDEND is non-transforming: preserve raw prices and quantity, add no cash
credit, adjustment, or reinvestment. P&L is **price-only execution P&L, not total
return**. This retains US5B/US7B raw-price semantics.

## Entry, holding, and outcome semantics

`valid_until` is only the entry deadline. Session close never liquidates a Swing
position. Entered positions retain the original entry and can complete later;
if evidence ends first they remain OPEN. Pending entries remain INCOMPLETE until
explicit coverage through expiry establishes NO_FILL.

Entry bars follow shared semantics: start at/after admission and end at/before
expiry. While entry remains unresolved, an admission/expiry boundary bisecting a
trading bar is rejected as `ENTRY_BOUNDARY_BISECTS_BAR`; no intrabar fill timing
is inferred. Once a prior checkpoint proves OPEN, a later expiry bisection is
irrelevant to the position and does not prevent continuation.

Explicit time/session-end exits retain M6.1 ordering: opening-gap stop/target
precedes a scheduled exit. A due boundary without an actual executable bar does
not fabricate a fill. Unsupported geometry returns `UNSUPPORTED_ENTRY_GEOMETRY`.
Malformed, incomplete, conflicting, or unavailable evidence raises ValueError;
callers must surface it rather than silently omit the observation or call it NO_FILL.

## M7.1 and slippage scenarios

The US performance builder requires exact equality with independently regenerated
US replay result/provenance, then supplies the accepted shared input/result to
ReplayPerformanceObservation. Strategy identity comes from the plan; regime comes
from the original canonical risk snapshot. M7.1 independently recomputes shared
replay equality, uses exit market date for months, and exit known-at/interval/plan
ID for realized drawdown. OPEN/INCOMPLETE/NO_FILL/REJECTED remain non-realized.

Every scenario must independently pass the US builder. Do not reuse another
scenario's compatibility decision or wrap an INCOMPATIBLE result as shared
execution evidence. Shared M7.1 still allows only its four slippage fields and
requires identical underlying replay paths. If consumed prefixes differ, M7.1
rejects the comparison; this adapter does not realign or extend scenario paths.

## Validation and limitations

Focused tests are engineering fixtures only. Regression gates include US7A,
US7B, US1/US2/US4, M5, M6.1/legacy M6, M7.1/legacy M7, downstream M8, full pytest,
compile, whitespace, static I/O scan, and unchanged shared implementation checks.

No shared contracts were modified. The adapter intentionally does not support
partial sessions, ambiguous same-day actions, corporate-action transformations,
or checkpoints that cannot satisfy shared availability/coverage requirements.
Input evidence and research exclusions remain subject to upstream attestation
and later research-validation review. This milestone does not establish alpha,
real-money readiness, or a completed US8 research-validation design.

## Validation snapshot

Base: `4f9bb59bd18ff0272c5bb8b9800764f5766ab84e`, branch
`agent/us-market-foundation`. All Python commands ran from the US workspace
using the authorized shared offline interpreter with `PYTHONDONTWRITEBYTECODE=1`.

| Gate | Result |
| --- | --- |
| US7C-B focused | 98 passed |
| US7A / US7B | 53 passed |
| US1 / US2 / US4 | 111 passed |
| M5 portfolio/risk/domain | 158 passed |
| M6.1 / legacy M6 | 142 passed |
| M7.1 / legacy M7 | 162 passed |
| Downstream M8 / M8b | 181 passed |
| Full `pytest -q` | 1685 passed in 28.77s |
| `py_compile` for both modules and the new test file | passed |
| `git diff --check` plus no-index checks of all four additions | clean |
| Static I/O, float-conversion, sorting, and random-ID call scan | no candidates |
| Shared and legacy implementation/test diff against base | unchanged |

No network/provider calls, operational DB/schema changes, package installations,
Docker/service operations, broker execution, staging, commits, or pushes occurred.
Regression DB writes were limited to the existing disposable test harness.

Manual commit proposal: `feat: bind US historical execution replay`.
Stage only `app/us/paper_replay_models.py`, `app/us/paper_replay.py`,
`tests/test_us7c_b_paper_replay.py`, and this document. After a clean manual commit,
US8 can begin as a separately scoped research milestone; this change does not
itself supply historical validation data or a real-money-readiness conclusion.
