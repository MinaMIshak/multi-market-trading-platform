# US8 Replay-Aware Research Validation

## Scope

US8 is an additive, US-specific historical research-validation layer.

It consumes canonical US7C-B replay requests/results and preserves their
point-in-time evidence, stable security identity, session history, corporate
action history, risk admission and execution semantics.

US8 does not acquire market data, write operational state, alter production
services, place broker orders, or make live-readiness decisions.

The evaluation path is:

US7A planning/risk
-> US7C-B historical replay
-> M7.1 replay performance
-> US8 development/holdout validation
-> final frozen research-validation artifact.

Legacy M8 contracts remain unchanged.

## Candidate and dataset truth

`USReplayResearchCandidate` binds one exact replay-evidence version for one
trade and optional execution-sensitivity scenario.

A candidate identity binds:

- candidate schema version
- scenario ID
- exact request identity
- exact replay-result identity

The baseline uses `scenario_id=None`.

Candidate construction revalidates the US7C-B replay result. Replay
incompatibility remains explicit; US8 never manufactures M7.1 observations
from incompatible evidence.

`USReplayResearchDataset` canonicalizes the complete supplied candidate set
before any as-of filtering. Therefore malformed future evidence cannot be
hidden by an earlier evaluation cutoff.

Datasets require one exact strategy identity and deterministic candidate
ordering. Candidate versions are unique by scenario, trade-plan identity and
evidence cutoff.

## Point-in-time selection

Research selection is controlled by explicit evaluation cutoffs.

For each scenario/trade pair, US8 selects the latest canonical candidate whose:

- evidence cutoff is at or before the evaluation cutoff
- request build timestamp is visible to the caller's runtime research build

Development training selection additionally requires evidence to be strictly
before the fold test start. Equality is purged.

Decision-time membership remains half-open and outcome-independent. Completion,
PnL, economic label, market regime and later evidence do not decide whether a
trade belongs to a development test or frozen holdout interval.

## Development evaluation

Development uses explicit walk-forward folds.

For each fold, US8 records:

- training selections visible strictly before test start
- baseline OOS test membership
- replayed and incompatible observation IDs
- exact M7.1 replay-performance results
- declared research-evidence criteria
- deterministic bootstrap configuration and results

Bootstrap economics operate on already validated completed replay observations;
US8 does not replay trades inside bootstrap replications.

Required execution-sensitivity scenarios must exactly cover the admitted
baseline OOS set. Missing, future-only, incompatible or otherwise unavailable
scenario evidence produces explicit incomplete coverage. Profitable subsets
are never evaluated as complete scenarios.

Scenario economics are delegated to M7.1, which permits only its declared
slippage-field changes. US8 does not recompute scenario economics separately.

## Frozen holdout evaluation

Holdout membership is:

`holdout.start <= decision_at < holdout.end`

The holdout evaluation cutoff must be at or after the holdout end.

Holdout evaluation is separate from development evaluation. It does not run
development bootstrap logic and does not apply the development-only bootstrap
lower-bound criterion.

Baseline incompatibility and scenario incompleteness remain explicit.
Required scenarios must exactly cover the same eligible baseline holdout set;
subset evaluation is prohibited.

## Frozen final protocol

`USReplayFrozenResearchProtocol` freezes the semantic inputs required to
reproduce the research decision:

- protocol ID
- strategy ID and version
- walk-forward plan and frozen holdout
- M7.1 performance configuration
- bootstrap configuration
- declared research-evidence criteria
- canonical required scenario IDs
- development evaluation cutoff
- holdout evaluation cutoff

The development cutoff must be at or after the final development test end and
must not enter the frozen holdout.

The holdout cutoff must be at or after holdout end and cannot precede the
development cutoff.

Both cutoffs are part of protocol identity because they determine which
historical evidence versions are admissible.

The caller's runtime `research_built_at` is a visibility guard, not a semantic
protocol parameter.

## Selected evidence manifest

The final report records the exact candidate identities that materially
influenced the result.

The manifest distinguishes:

- development baseline selections
- per-fold development training selections
- development scenario selections
- holdout baseline selections
- holdout scenario selections

Training selections are retained separately because a historical candidate
version visible before a fold test start can affect purge/training
classification even when a later version is visible at the overall
development cutoff.

Unrelated candidates and evidence versions after the frozen cutoffs do not
enter the semantic report identity.

## Final report identity and status

`USReplayResearchValidationReport` binds:

- the complete frozen protocol
- the exact development validation report
- the exact frozen holdout validation report
- the canonical selected-evidence manifest
- the final evidence status

`report_id` is a deterministic SHA-256 semantic identity over those artifacts.

The local runtime build clock is deliberately excluded. A later runtime build
with the same selected evidence produces the same report identity, while a
different selected historical request/result snapshot changes the identity.

Canonical reconstruction rejects a forged report ID, protocol/report mismatch,
unknown fold/scenario references, malformed selected evidence, or omission of
required baseline selections.

Final status uses insufficient-evidence dominance:

1. if development or holdout is `INSUFFICIENT_EVIDENCE`, final status is
   `INSUFFICIENT_EVIDENCE`
2. otherwise, if either is `FAILS_DECLARED_CRITERIA`, final status is
   `FAILS_DECLARED_CRITERIA`
3. only complete passing evidence yields `MEETS_DECLARED_CRITERIA`

## Safety properties covered by tests

US8 tests cover, among other cases:

- canonical full-dataset validation before filtering
- strict training evidence timing and equality purge
- half-open decision-time development/holdout membership
- replay incompatibility preservation
- exact scenario-set coverage
- future-only scenario evidence not repairing current coverage
- no profitable-subset evaluation
- unauthorized non-slippage scenario mutation rejection
- caller-order invariance
- future-version invariance
- runtime build-clock invariance
- semantic selected-snapshot sensitivity
- malformed future candidate fail-closed behavior
- frozen protocol chronology
- protocol/artifact binding
- forged final report identity rejection
- explicit insufficient-evidence dominance

At the completion of the US8 implementation gate, the focused US8 suite
contained 68 passing tests. The US8 plus M7.1, M8B, M8, US7C-B and M6.1
dependency regression contained 469 passing tests.

## Readiness boundary

US8 establishes engineering and research-validation infrastructure. It does
not establish profitable alpha, empirical robustness, paper readiness or live
readiness.

A real research decision still requires authentic point-in-time historical
evidence, including survivorship-safe membership and removed securities,
corporate actions and symbol changes, realistic liquidity/fill assumptions,
fees/slippage/gaps, sizing and exposure constraints, walk-forward/OOS testing,
an untouched frozen holdout, severe regime stress such as 2008 and 2020, and
subsequent paper-forward evidence.

Passing fixture tests is therefore evidence of software-contract correctness,
not proof of trading profitability or deployability.
