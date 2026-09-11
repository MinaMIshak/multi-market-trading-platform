# US7A Identity-Safe Research Planning and PIT Risk Admission

US7A connects a non-executable US6 Swing WATCH result to the shared M5
risk engine while preserving stable US instrument identity.

US7A is research-only.

It does not create fills, positions, paper simulations, performance
observations, broker orders, or live execution artifacts.

## Boundary

The US7A flow is:

US6 `USSwingResearchResult`
→ explicit `USResearchPlanTerms`
→ immutable `USResearchTradePlan`
→ PIT `USResearchRiskSnapshot`
→ shared `RiskEngine`
→ immutable `USResearchRiskAdmission`

A M5 `APPROVE` or `REDUCE` decision remains non-executable in US7A.

Every US7A plan and risk admission carries:

- `validation_status = UNVALIDATED`
- `execution_allowed = False`

## Explicit planning semantics

US6 produces strategy confirmation and a raw planning-close reference.
It does not define executable entry, stop, target, or validity rules.

US7A therefore requires explicit `USResearchPlanTerms`.

No stop percentage, target multiple, entry band, or holding period is
silently inferred from the US6 close.

The caller must explicitly provide:

- planning rule version;
- entry low;
- entry high;
- entry reference;
- stop;
- target 1;
- optional target 2 and target 3;
- validity horizon.

The existing shared `TradePlan` geometry contract is reused to validate
the explicit plan.

US7A currently admits LONG planning only because the US6 Swing research
result is LONG research and the shared M6 execution path does not yet
support SHORT replay.

## Stable identity

Legacy `TradePlan` and `RiskDecision` are shared domain models and do not
carry stable US instrument identity.

US7A therefore does not use ticker identity as the research identity.

`USResearchTradePlan` preserves:

- stable `instrument_id`;
- historical canonical symbol;
- listing MIC;
- US6 source signal identity;
- US5B source dataset identity;
- strategy version;
- strategy config identity;
- explicit planning-rule identity.

The legacy `TradePlan` is materialized only at the shared M5 boundary
with deterministic signal and trade-plan UUIDs.

The same US7A research inputs therefore produce the same semantic plan
and deterministic IDs.

## PIT risk snapshot

`USResearchRiskSnapshot` contains the risk and portfolio facts supplied
for one historical decision.

Its `available_at` must not be later than the risk decision timestamp.

The risk decision itself cannot precede the research plan creation time.

This preserves the information-time ordering:

signal known
→ research plan created
→ PIT risk facts available
→ risk decision

A later decision after plan expiry is allowed to reach the shared M5
engine, which can explicitly return its existing `PLAN_EXPIRED` block.

The snapshot is caller-supplied PIT research evidence. US7A validates the
consumed structure and timing but does not claim cryptographic source
authenticity.

## Stable-instrument exposure

The shared M5 engine has a legacy field named `symbol_exposure_value`.

For US7A this compatibility slot is populated from
`instrument_exposure_value`, whose required meaning is exposure to the
stable US instrument identity, not exposure reconstructed from the
current ticker text.

This prevents a symbol change from incorrectly resetting same-instrument
risk exposure.

The US wrapper preserves that stable-instrument value explicitly in the
US risk admission.

## Shared M5 reuse

US7A reuses `RiskEngine` unchanged.

The shared arithmetic continues to own:

- risk-per-trade budget;
- market-regime scaling;
- position-value cap;
- portfolio-exposure cap;
- portfolio-open-risk cap;
- stable-instrument exposure cap through the compatibility slot;
- optional correlation-group cap;
- optional liquidity cap;
- cash capacity;
- maximum open-position gate;
- daily realized-R kill switch;
- minimum target-1 reward/risk gate.

Risk policy values remain explicit research choices and are not claimed
to be empirically validated US trading policy.

## Whole-share sizing

The existing M5 engine floors quantity to whole shares.

US7A intentionally preserves that behavior.

Fractional-share execution is not inferred merely because some US brokers
support it. Fractional sizing requires a separately specified broker and
execution capability contract.

## Deterministic research identity

US7A semantic identities are SHA-256 based.

The deterministic signal, trade-plan, and risk-decision UUIDs are derived
from semantic research inputs rather than random runtime UUID generation.

This makes repeated historical replay reproducible.

The mutable shared domain objects are materialized only at compatibility
boundaries; immutable US research contracts remain the canonical US7A
research records.

## Execution boundary

US7A does not create `PaperSimulationInput`.

It does not import or call the M6 simulator.

Daily US5B OHLCV is not converted into synthetic intraday execution data.

Historical execution remains blocked until:

US7B establishes authentic historical US intraday evidence/admission, and
US7C binds that admitted execution stream to the existing M6 simulator.

Only then can genuine M6 results feed shared M7 performance analysis.

## Non-goals

US7A performs no:

- provider or network acquisition;
- database writes;
- production integration;
- scheduler work;
- broker integration;
- live orders;
- intraday-bar synthesis;
- paper fills;
- paper positions;
- performance analysis;
- walk-forward evaluation;
- frozen-holdout evaluation.
