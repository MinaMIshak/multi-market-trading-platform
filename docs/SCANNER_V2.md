# Scanner v2 — first contract and card slice

Status: offline contract, read-only card and bounded EGX verification coverage
adapter and EGX-RANK-v1 reporting adapter validated offline; admitted binding
producer and runtime/API wiring pending.
No strategy, model, risk policy, scheduler or Champion is activated or promoted.
LIVE_MONEY=DISABLED.

## Reused integration points

- `app.strategies.contracts.Candidate` retains setup identity, version, configuration,
  detection/cutoff, evidence and research-only semantics.
- `app.domain.models.TradePlan` validates the explicit decimal long plan geometry.
  Float candidate references are not converted into trading terms.
- `app.paper.shadow_candidate_admission` remains a separate explicit admission
  boundary. Creating a TradeDecision or card never calls admission or RiskEngine.
- `app.egx_scan` / `app.egx_universe_scan` already coordinate scoped coverage.
  The next adapter should reuse their accounting, while retaining US-specific
  identity/calendar/source admission through existing US research adapters.
- `app.ui.trade_card.render_trade_card` renders a read-only HTML fragment;
  `trade_card` provides its JSON-ready projection. No HTTP route is installed yet.

## Contract

`trade-decision-v1` binds the existing candidate to an explicit EGX/US source,
canonical instrument UUID, artifact hash, evidence references, completed-bar flag,
data availability and freshness window, and immutable decimal entry/stop/target
terms. Max holding sessions are required; flat-by is explicit and optional.
Only confirmed setup states can produce this reporting record. WATCH and invalid
setups are excluded. Risk status remains NOT_EVALUATED, confidence UNVALIDATED,
event risk UNKNOWN, and execution disabled. The deterministic digest covers all
serialized fields, including market, candidate evidence and source identity.

Input timestamps and price terms are preserved, not normalized retrospectively.
Identity means identical serialized contract content; alternate timestamp offsets
or decimal representations are not promised to yield the same digest.

Nested fields are immutable tuples/scalars/frozen models. The projection revalidates
unchecked model copies, labels expired/stale records without changing the original,
and rejects observation times before detection. HTML escapes all supplied text.

## Remaining gates

Source metadata is an adapter assertion, not proof of authenticity, licensing,
calendar correctness, exchange membership, or market-specific identity admission.
The market-specific adapters must bind these assertions to existing admitted
artifacts before runtime use. Candidate itself has no market field; the new
explicit source binding must be made by that trusted adapter, never guessed from
symbol text. Synthetic tests do not establish market coverage or provider health.

`app.scanner_v2.scan_egx` now delegates to `scan_egx_scope` with a bounded,
unique explicit selection (maximum 1,000), preserving its admission and coverage
semantics. It returns deterministic lexical ordering, requested/usable/scanned
counts, per-symbol exclusions and an unknown authoritative universe size.
Usable means verification passed at the verifier's decision timestamp, not current
provider health. Extra out-of-scope source/error keys and inconsistent coordinator
reports are rejected. Only fixed exclusion codes enter the projection.

The current legacy SWING verifier emits WATCH or READY_NO_SIGNAL only. Both are
excluded from the confirmed trade-decision contract; this adapter therefore emits
zero candidates and no cards. It does not accept caller-supplied trade decisions
as verified output. No WATCH-to-READY conversion is authorized. The coordinator's
existing audit-event write remains; no scan history or signal is published by this
wrapper. Synthetic mixed-status and 224-symbol fixtures are not live scan evidence.

Next bind an existing confirmed research setup to market-specific admitted inputs,
explicit decimal terms and the contract, with inspected artifact provenance.
Do not invent missing targets, triggers or risk decisions. Keep missing required
inputs visibly excluded. Then connect the read-only API/UI with coverage evidence.
No new breakout/ORB/RSI generators are part of this slice. The complete Scanner v2
milestone remains pending those adapters, runtime integration and evidence checks.

## EGX-RANK-v1 decision adapter (validated offline)

`app.scanner_v2_egx_rank.bind_egx_rank_report` projects one published
`egx-ranking-report-v1` artifact into trade-decision-v1 records. It is the first
confirmed-setup adapter: EGX-RANK-v1 already publishes explicit decimal entry
zone, entry reference, stop, T1 and T2 for STRONG_CANDIDATE/CANDIDATE.

- The artifact bytes must match an expected SHA-256 taken from a verified
  snapshot manifest. Report schema, rank version, admission, LIVE_MONEY=false,
  session, counts and record uniqueness are checked; drift fails the whole call.
- Published terms are copied exactly as decimal strings. Nothing is recomputed,
  rounded or invented. `target` is T1, which matches `TradePlan.target_1` and the
  simulator's half exit. T2 and the breakeven rule are carried in candidate
  evidence.
- `max_holding_sessions` = `MAX_HOLD + 1` = 11. `simulate()` exits at index
  `MAX_HOLD`, and index 0 is the fill session.
- The trigger is a new literal, `EGX_RANK_V1_NEXT_SESSION_ZONE_V1`. The
  champion's lifecycle rule (next session only, gap below zone = NO_FILL) is
  not the forward-shadow `LONG_ENTRY_ZONE_TOUCH_V1` rule, so it is not labeled
  as that rule.
- The trusted caller (`EgxRankBinding`) supplies the instrument UUIDs, session close,
  freshness window, next-session open and validity, from admitted security-master and
  calendar evidence. The report's `next_expected_session` is an unverified
  expectation and is kept only as evidence.
- WATCHLIST becomes SETUP_NOT_CONFIRMED and NO_TRADE becomes NO_TRADE. An
  unbound identity gives IDENTITY_UNBOUND, and invalid or missing terms give
  TERMS_INVALID. All of these are visible exclusions. A candidate record that
  contradicts the champion gates is schema drift and fails closed.
- Mapping champion CANDIDATE classes to Candidate state READY is a reporting
  label. It is not admission, risk approval or evidence of edge. Risk stays
  NOT_EVALUATED, confidence UNVALIDATED and promotion NOT_AUTHORIZED. The
  supervisor context records that the champion's trade construction is
  currently net-negative, and these cards must not suggest otherwise.

There is no runtime caller, binding producer, HTTP route or persistence yet.
Synthetic tests do not establish market coverage.

SYSTEM's existing Project checkpoint reports this slice and its limitations via
PROGRESS.json; it does not imply that the feature is deployed or scanning.

Validation of the preserved handoff found eight invalid-term fixtures accidentally
bound an out-of-scope identity; those fixtures now exercise term rejection with
valid scope. Source/candidate/contract failures propagate, while only decimal
parsing and term geometry failures become TERMS_INVALID. Stops must be below the
whole zone, T1 above it, T2 above T1, and collapsed zones are excluded.
Duplicate JSON keys and non-finite numbers reject the report, including unbound
records. Generation must precede the admitted next-session open; its timestamp
is retained in immutable evidence. This is a historical research projection,
not a statement that entry remains possible when a card is viewed later.

The projected constants have a literal configuration digest and drift guard;
this identifies the adapter's interpretation, not a producer-attested code digest.
Historical artifacts do not contain a producer configuration digest. Future
producer changes require explicit versioning and compatibility review.
No corporate-action clearance is inferred from split-adjusted prices: event risk
remains UNKNOWN. Missing daily artifact IDs omit that reference; ranking-byte
provenance remains, without claiming independently verified underlying bars.

Before API/UI integration, bind admitted identities/calendar and include verified
lifecycle evidence (sample size and measured net result), preserving negative or
inconclusive findings. No such performance artifact was verified in this cycle.
The handoff barrier remains for the supervisor to clear after commit; no route,
activation, new setup logic or strategy promotion is included.

## Historical identity projection

`app.scanner_v2_identity.project_egid_identities` reconstructs exact canonical
EGX identities from bounded preserved EGID bytes and a caller-trusted
`RawArtifactManifest`. It reuses `build_canonical_security_master`, verifies
hash/size/count/provider/asset/session and availability by observation time,
and rejects ambiguous, malformed or non-finite input. Requested symbols are
exact canonical identifiers; aliases are not guessed. Missing identities and
non-equities remain explicit exclusions. The immutable projection can supply
identity tuples and hash provenance to the existing rank binding and Trade Card.

This is **not admission**: ingestion receipt does not establish successful
security-master refresh admission, licensing, listing membership or validity
between snapshots. The mutable repository replaces snapshots and cannot supply
historical admission evidence. A same-session snapshot is required conservatively;
no stale-snapshot fallback or validity interval is invented. The projection has
`admission=NOT_ESTABLISHED`, does no IO and cannot activate the adapter.

Next persist/bind independently verifiable admission evidence and admitted calendar
windows before adding a runtime caller. In particular, the current `MarketSession`
status alone does not prove that a next-session schedule was known before entry.
Do not substitute the ranking report's next-session expectation for that evidence.
Lifecycle evidence and API/UI wiring also remain pending. The prior handoff was
committed by the supervisor; its barrier marker was absent at this cycle start.
