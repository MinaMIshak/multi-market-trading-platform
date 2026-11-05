# Scanner v2 — first contract and card slice

Status: offline contract, read-only card and bounded EGX verification coverage
adapter implemented; confirmed-setup adapters and runtime/API wiring pending.
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

SYSTEM's existing Project checkpoint reports this slice and its limitations via
PROGRESS.json; it does not imply that the feature is deployed or scanning.
