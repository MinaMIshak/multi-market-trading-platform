# M8 research validation — M8A partitions and M8B evidence

## Status and scope

M8A implements offline, in-memory executable observations, explicit purged rolling
walk-forward contracts, and a frozen future holdout boundary. M8B adds the separate
evaluation and evidence contracts documented below.
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

M8B now implements OOS aggregation/reporting, regime-analysis integration,
bootstrap/confidence analysis, a separate explicit holdout evaluation and a final
research report. The approved M8A partition function itself still computes no
profitability metrics, confidence intervals or significance tests.

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


## M8B status: engine versus market evidence

**M8 VALIDATION ENGINE COMPLETE - REAL MARKET VALIDATION NOT YET EXECUTED.**

A. The offline validation engine is implemented and tested with deterministic
engineering fixtures. B. Actual historical market validation has **not** been
executed. Fixture economics must never be cited as market evidence. This is not
completion of M8 strategy validation or a recommendation for real-money readiness.

A real run still requires an authentic canonical `ResearchDataset` for one fixed
strategy ID/version: point-in-time admissions and universe/strategy provenance,
canonical M6 inputs and replay-consistent results with executable bars, explicit
costs/slippage and risk sizing, and truthful supplied regime metadata. It also
requires a predeclared rolling plan with untouched future holdout, starting equity,
bootstrap assumptions, and criteria. This implementation acquires no dataset and
certifies neither historical authenticity nor absence of upstream look-ahead.

## M8B public API and protocol

The added exports from `app.research` are:

- `BootstrapConfig`, `ResearchEvidenceCriteria`, `FrozenResearchProtocol`.
- `ConfidenceInterval`, `BootstrapReport`, `CriterionCheck`, `EvidenceStatus`.
- `FoldOOSReport`, `DevelopmentOOSReport`, `HoldoutEvidenceReport`,
  `ResearchEvidenceReport`.
- `evaluate_development_oos(dataset, partition, protocol)`.
- `evaluate_frozen_holdout(dataset, protocol)`.
- `build_research_evidence(dataset, partition, protocol, development, holdout)`.

All models are strict, frozen, finite-valued Pydantic contracts. Config/protocol
versions are required; report schema versions have literal v1 defaults. The
protocol binds an explicit nonblank protocol ID, strategy ID/version, exact M8A
`WalkForwardPlan` (including `FrozenHoldout`), M7 `PerformanceConfig` with positive
starting equity, bootstrap config, and evidence criteria. Canonical nested settings
are reconstructed on construction and at evaluation boundaries. Dictionaries are
not substitutes for these canonical input contracts.

Freezing is **not cryptographic proof of preregistration**. It cannot prevent a
human creating another protocol after inspecting outcomes, or enforce a single
holdout use across processes. One protocol and one strategy identity/version are
evaluated at a time. There is no ranking, optimizer, search, threshold adaptation,
strategy selection, or readiness automation. Changing a strategy after inspecting
holdout requires a new research cycle and new untouched future evidence; the old
holdout cannot be represented as untouched validation for that change.

## Development membership, performance and regimes

The callable reconstructs the canonical dataset and protocol, checks strategy
identity/version, recursively checks supplied partition contracts, recomputes
`partition_development(dataset, protocol.plan)`, and requires exact equality with
the supplied partition. It resolves **only each fold's `test_ids`** to canonical
research observations. Duplicate dataset/OOS identities, missing IDs, forged
partitions, changed plans, malformed model-copy/model-construct objects, and
inconsistent M6 replay fail closed. No missing or malformed row is dropped.
Training, purged and unavailable-training IDs never serve as OOS economic evidence
by virtue of training membership. A row independently admitted as a test by M8A
may also be a training candidate for another fold, preserving M8A semantics.

Every admitted test observation remains counted regardless of completion, late
label availability, regime or economic result. Each fold embeds a canonical M7
`PerformanceReport`, as does the overall union of nonoverlapping test IDs. Reports
include observation IDs in M8A decision-time/string-UUID order, fold count, and
positive/negative/flat fold counts by completed net P&L. Folds with no completed
trades appear in `undefined_fold_ids` and never count as flat economic folds.

Embedded M7 reports preserve exactly:

- total observation, completed, rejected, no-fill, open and incomplete counts;
- economic net win/loss/breakeven counts, gross P&L, costs and net P&L;
- completed-only net expectancy, R expectancy, profit factor, average win/loss,
  and economic win rate (not an evidence objective);
- realized max drawdown amount and fraction (`max_drawdown_pct` is a fraction,
  not percentage points), starting/ending/peak equity;
- average MAE/MFE and MAE/MFE in R;
- monthly economics by M7 admission `market_date` year/month, with completed-only
  positive/negative/flat monthly consistency;
- explicit supplied regime buckets, sorted by enum value, including `UNKNOWN`
  when present. Every bucket retains noncompleted counts and completed-only
  economics. Regimes are never inferred, recomputed, selected or filtered.

Each fold and the overall union starts at the same declared M7 starting equity.
Overall drawdown is recomputed by M7 over the union, never summed across folds.
M7 empty totals/drawdown remain zero; undefined expectancies/profit factor remain
null. A zero empty drawdown is bookkeeping, not evidence of controlled risk.
All-win or all-flat profit factor is null, never infinity. No periodic-return
series is invented: embedded Sharpe/Sortino remain unavailable, and no new
slippage scenarios are run. Existing M6 cost/slippage outcomes are retained.

## Bootstrap configuration, sampling and confidence intervals

Every statistical setting is required: `config_version='research-bootstrap-v1'`,
`seed` (exact Python integer, including negative integers), `replications` (strict
positive integer), `confidence_level` (finite Decimal strictly between 0 and 1),
and `block_size` (strict positive integer). Booleans, integer-like strings/floats
and nonfinite values are rejected. There is no implicit seed, confidence level,
replication count, block size or IID assumption.

Only completed **development OOS** outcomes enter bootstrap. Noncompleted states
are not zero-return samples. The order reuses M7's exact `_exit_key`:
`(exit.known_at, exit.interval_start, str(trade_plan_id))`. The bootstrap report
records that ordered ID sequence and sample count. Already-canonical net P&Ls are
extracted as immutable Decimal primitives; no M6 resimulation, Pydantic rebuilding,
or M7 analyzer invocation occurs inside a replication. Ordinary reports and public
boundary validation continue through canonical M7/M8A, outside the replicate loop.

A local `random.Random(seed)` chooses starts uniformly from `0..n-block_size`.
Blocks **do not wrap**. Each contiguous block is appended until at least n outcomes
have been selected, and the final sequence is truncated to exactly n. Block size 1
is explicitly IID by completed trade. Larger sizes preserve within-block chronology
but not dependence across block joins. The caller owns whether the choice is
statistically appropriate; this is not a calendar-time or portfolio block model.
A block equal to n repeats the original sequence and may yield degenerate intervals;
that does not establish certainty. Source rows and global random state are untouched.

Primitive arithmetic uses the same independent precision-34 Decimal context as M7.
Replicates produce total net P&L, mean net P&L (net expectancy), and realized maximum
drawdown amount using declared starting equity, running equity and running peak.
Bootstrap deliberately omits profit factor and drawdown-fraction intervals; their
ordinary point estimates remain in M7 reports. No infinity or undefined-replicate
substitution is used.

The central percentile interval uses tail probability `(1-confidence_level)/2`.
For B sorted replicates and probability p, the quantile linearly interpolates at
index `(B-1)*p` between floor and ceiling indices. No bias correction, studentization,
normality assumption or library quantile defaults are introduced. Each metric
records actual-sample estimate, lower/upper bounds, valid replication count, and
unavailable reason; shared seed/count/confidence/block settings are in
`BootstrapReport.config`. An empirical interval need not contain its point estimate;
only lower <= upper is guaranteed. Small replication counts (including one) are
accepted as explicitly requested and can produce coarse/degenerate intervals.

## Insufficient evidence and declared criteria

Bootstrap reason precedence is `ZERO_COMPLETED`, then `ONE_COMPLETED`, then
`BLOCK_EXCEEDS_SAMPLE`. Each returns null bounds and zero valid replications.
For zero completed trades, estimates are also null; for one or an oversized block,
observed point estimates remain available but no confidence interval is fabricated.
The block is never silently reduced. A single realized trade may have an observed
M7 drawdown but cannot support this contract's drawdown uncertainty estimate.
Missing bootstrap evidence makes development status insufficient even when no
lower-bound criterion was enabled.

`ResearchEvidenceCriteria` explicitly requires:

- `config_version='research-evidence-criteria-v1'`;
- `minimum_completed_development_trades` and `minimum_completed_holdout_trades`,
  each a strict integer >= 2 (the engine's minimum evidence floor);
- finite Decimal `minimum_net_expectancy` and nonnegative finite Decimal
  `maximum_drawdown_fraction`;
- `minimum_profit_factor`: nonnegative finite Decimal or explicit `None`;
- `minimum_net_expectancy_lower_bound`: finite Decimal or explicit `None`.

There are no default economic thresholds. Minimums use inclusive >=; drawdown uses
inclusive <=. Net expectancy, drawdown and optional profit factor thresholds apply
to **both** development overall OOS and holdout separately. The optional lower
confidence bound is explicitly **development-only**, because bootstrap samples only
development OOS, never holdout. Fold and regime metrics are descriptive; no hidden
per-fold/per-regime pass requirements or hindsight exclusions exist.

Each criterion records actual, threshold, comparison, passed (true/false/null) and
unavailable reason. Below the declared completed-trade minimum, all checks are
unevaluable (`BELOW_DECLARED_SAMPLE_MINIMUM`), rather than treating undersampling as
economic failure. Otherwise an undefined required metric is `UNDEFINED_METRIC`.
Status is `INSUFFICIENT_EVIDENCE` when required evidence is unavailable, then
`FAILS_DECLARED_CRITERIA` if any evaluable required criterion fails, and only
`MEETS_DECLARED_CRITERIA` if every required criterion is evaluable and passes.
Missing evidence takes precedence over failures. Bootstrap unavailability is also
explicitly recorded in the development bootstrap report, outside criterion checks.

## Separate holdout and final reporting

Development evaluation never invokes the holdout evaluator. The explicit holdout
API uses only `holdout.start <= decision_at < holdout.end`, sorted by decision time
and string UUID. Neither completion, economic outcome, regime nor label availability
controls admission. Noncompleted holdout counts remain visible; economics are M7
completed-only. It uses the exact canonical frozen protocol, including declared
starting equity and criteria, and neither bootstraps holdout nor changes settings.
Canonical integrity is still checked for the entire supplied dataset in either API.

The final builder accepts separately produced development/holdout reports, or
explicit `None` for missing evidence. It revalidates report descendants and audits
supplied reports by exact recomputation against the canonical dataset, partition
and protocol, rejecting tampering, changed inputs and settings. A supplied holdout
report triggers a repeat of that explicit holdout evaluation for integrity checking;
an absent report stays absent and does not trigger holdout evaluation. No missing
evidence is manufactured. Retain the canonical dataset for this audit.

The final report embeds the complete protocol, separate evidence and their checks,
status and limitations. Missing either report or insufficient status in either
report yields insufficient final evidence; otherwise any failed criterion fails
the final status. Every required check must pass for a meeting status. This is
never a readiness approval, guaranteed profitability, proven alpha or guaranteed
robustness; human review and further operational validation remain necessary.

Isolation tests change development-only observations while retaining identical
holdout evidence, and replace holdout outcomes while retaining equal development
partition/report and unchanged protocol. Tests also cover strict configurations,
M7 arithmetic equivalence, noncompleted counts, explicit UNKNOWN regimes, late
labels, exit ordering, deterministic block/quantile behavior, ambient random and
Decimal independence, corruption rejection, missing evidence and network denial.
A replay-call-count test proves replication growth does not grow M6 replay calls.
