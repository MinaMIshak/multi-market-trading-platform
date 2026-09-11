# M8 research validation — M8A boundary

## Status and scope

M8A implements offline, in-memory executable observations, explicit purged rolling
walk-forward contracts, and a frozen future holdout boundary. It stops before M8B.
M0–M7 semantics, routes, UI, dependencies and operational wiring are unchanged.

**M8A alone establishes no profitability, alpha, robustness, statistical
significance, or real-money readiness.** Test fixtures establish engineering
contract behavior only; they are not market data or research-validation evidence.

## Public API

`app.research` exports:

- `ResearchObservation` (`research-observation-v1`): canonical M7 wrapper with
  derived observation ID, admission time, label availability and economic label.
- `ResearchInterval`: explicit UTC `start`, `end` interval, used in versioned contracts.
- `WalkForwardFold` (`walk-forward-fold-v1`): `fold_id`, `train`, `test`.
- `FrozenHoldout` (`frozen-holdout-v1`): `holdout_id`, `interval`.
- `WalkForwardPlan` (`walk-forward-plan-v1`): `plan_id`, ordered nonempty `folds`,
  and `holdout`.
- `ResearchDataset` (`research-dataset-v1`): explicit `strategy_id`,
  `strategy_version`, tuple of observations, including an explicitly empty tuple.
- `FoldPartition` (`fold-partition-v1`): fold definition; eligible `training_ids`,
  `purged_training_ids`, `unavailable_training_ids`, `test_ids`; corresponding
  derived counts.
- `DevelopmentPartition` (`development-partition-v1`): strategy identity, complete
  plan including frozen holdout identity/range, and ordered fold partitions.
- `partition_development(dataset, plan) -> DevelopmentPartition`.

Contracts use the existing strict, frozen Pydantic `Contract`. Schema literals
have versioned defaults; supplying another version fails. Identities must be
nonblank without surrounding whitespace. No plan, fold, duration or strategy
identity is inferred. Output serializes IDs/counts without copying market payloads.

## Executable observations and timing

`ResearchObservation.performance` retains the canonical `PerformanceObservation`:
M6 input, replay-verified result, strategy ID/version and attached market regime.
Its M6 input preserves the trade plan, risk admission, bars, explicit cost/slippage
config, symbol, market date, session ID, source ID and provenance ID. No raw-price
label constructor exists. `trade_plan_id` is the stable observation identity.

Derived `decision_at` is exactly `performance.paper_input.admission_time`. It is
paper admission, not an inferred M4 signal time, TradePlan `created_at`, a bar end,
or a wall clock. M6 already requires admission within plan validity. No new
candidate-to-trade mapping or strategy timestamp is invented.

For `state == COMPLETED`, `label_available_at` is exactly
`performance.paper_result.position.exit.known_at`. M6 explicitly sets this to the
executing bar's `available_at`, including fills at open. It may be later than the
exit interval end; neither interval start/end nor the final supplied bar is a
substitute. It must follow admission. M8A requires UTC admission and label times;
non-UTC aware values and naive values are rejected, not silently converted.
Existing M6 timestamp acceptance is unchanged. Callers may explicitly convert
canonical instants to UTC before constructing their artifacts.

Completed `economic_label` is WIN for net P&L > 0, LOSS for net P&L < 0, and
BREAKEVEN for exactly zero. These are economic labels, independent of M6 exit
mechanism: TIME_EXIT can have any economic sign, and costs may make a target exit
an economic loss. Labels retain the exact M6 cost/slippage assumptions and net
metrics. No gross-P&L substitution, new execution logic, slippage grid or
sensitivity calculation is introduced. Existing M7 replay verification invokes
unchanged M6 solely to validate the supplied input/result pair.

REJECTED, NO_FILL, OPEN and INCOMPLETE have `economic_label=None` and
`label_available_at=None`. No final economic timestamp or zero-return outcome is
invented. Their canonical state remains visible in `performance.paper_result`.

## Membership and purge rule

All intervals are half-open `[start, end)` with canonical UTC-aware timestamps
and `start < end`. Starts are included; ends are excluded.

For each fold, an eligible training row must satisfy both:

1. `train.start <= decision_at < train.end`.
2. A completed executable label exists and `label_available_at < test.start`.

The information cutoff is **strictly before `test.start`**. Equality is purged,
as is any later availability. With an explicit train/test gap, labels may become
known within that gap, provided they are strictly before the test boundary.
There is no additional default embargo or fixed-day purge horizon in M8A.

Training candidates with a label at/after the cutoff appear in
`purged_training_ids`. Candidates without a final economic label appear separately
in `unavailable_training_ids`; they are ineligible but are not misrepresented as
late completed outcomes. These sets and `training_ids` exhaust training decision
candidates. Empty sets and all-purged candidates produce empty eligible training
IDs and zero counts, without fabricated observations or metrics.

Test membership requires only `test.start <= decision_at < test.end`. It includes
noncompleted states and outcomes known after test end. No economic sign, regime,
completion status or label availability filters test admission. Test outcomes do
not construct folds, change earlier membership or choose strategy parameters.
Rows outside the explicit fold decision intervals contribute no membership.

Observation IDs within each set are deterministically ordered by admission time,
then string UUID. Input row permutation therefore gives equal partitions. Fold
order is preserved and never repaired by sorting. No clock, locale, random state,
hash iteration order, filesystem order or external data affects membership.

## Fold and holdout chronology

Fold IDs are unique. Each training interval ends at/before its test start. Test
windows must arrive in chronological order and cannot overlap; adjacent tests are
allowed. Across folds, training starts and ends cannot move backward. Overlapping
rolling training windows, equal training boundaries, and expanding training
windows are allowed. Window widths, gaps and frequency are entirely caller supplied.

The holdout interval and identity are explicit and frozen within the plan. Its
start must be **strictly greater than every development test end**. Even equality
is rejected under this strict future-boundary contract. Together with train-before-
test chronology, this excludes holdout decisions from every training/test interval.
A development training outcome becoming available in the holdout period necessarily
fails its earlier test information cutoff and is purged.

The development result records only the holdout contract, never holdout observation
IDs, counts, labels, metrics or aggregates. Adding, removing or replacing valid
holdout-only observations of the same strategy identity cannot change the result.
Dataset validation still checks canonical integrity, duplicate IDs and fixed
strategy identity for all supplied rows, including holdout rows. Replay validation
is not holdout evaluation: malformed artifacts fail closed, rather than being
ignored to obtain a result. No holdout outcome is used in membership logic.

Freezing is an immutable in-memory contract, not a persistent registry or proof of
preregistration. A caller can construct a different plan; external review must
ensure plans were fixed before outcomes were inspected. M8A provides no holdout
evaluation API or parameter-selection API.

## Canonical and identity safety

Canonical objects are required, rather than flattened shadow dictionaries. The
callable boundary reconstructs dataset, observations, plan, folds and intervals,
including inputs made via `model_copy`/`model_construct`. M7 replay verification
remains mandatory. Nested result positions, fills and metrics are explicitly
revalidated and dictionary substitutions rejected. Invalid data raises ValueError
(including Pydantic ValidationError), with no partial partition returned.

Duplicate trade-plan IDs are rejected anywhere in the dataset, even in holdout.
Every row must match the dataset's explicit strategy ID and version. Regimes are
retained as supplied metadata; they neither filter membership nor get recomputed.
M7 does not carry a regime availability timestamp or authenticated strategy/config
binding. M8A does not invent either: callers remain responsible for the truth and
point-in-time appropriateness of that metadata and for supplying one fixed strategy
configuration under its declared identity. Structural replay verifies execution
consistency, not authenticity of upstream data or strategy provenance.

Construction detaches canonical mutable domain descendants from source objects;
partitioning does not mutate its sources. Existing nested domain models are not
redesigned as deeply immutable objects. If callers later mutate a wrapper's domain
descendants, the next partition call revalidates them, failing closed on invalid
or replay-inconsistent changes. Retain the canonical dataset alongside the compact
partition result for audit; UUIDs alone are not content-addressed evidence.

## Deferred work and limitations

M8B will own OOS aggregation/reporting, regime-analysis integration,
bootstrap/confidence analysis, and a separate explicit actual holdout evaluation
and final research report. None is implemented here. M8A computes no profitability
metrics, confidence intervals or significance tests.

Parameter search, hyperparameter tuning, strategy optimization, candidate ranking,
ML, robustness/alpha claims and readiness decisions are absent. There is no RESEARCH
UI, data acquisition, persistence, production wiring or scheduled research job.
M6 limitations (paper assumptions, censored OHLC excursions, no tick-path authority,
no cross-session resume protocol) remain. No historical evidence is supplied or
certified by this milestone, and partitioning alone cannot prove that upstream
signals were created without look-ahead or that a caller never inspected holdout.

## Safety and verification

Implementation is pure Python using stdlib and existing project/Pydantic contracts.
No DB/schema changes, external API/network calls, EODHD, secrets, production access,
Docker changes, sudo, deployment, broker/live-money execution, scheduler work or
paper_refresh work is required or performed. All tests run under the repository's
network-blocking fixture; M8A also tests its callable with socket creation and DNS
lookup forbidden. Test data are explicitly engineering fixtures, not research evidence.

Focused verification includes M8A and relevant M6/M7 regression. The full regression
and tracked/untracked whitespace checks are required before human review.
