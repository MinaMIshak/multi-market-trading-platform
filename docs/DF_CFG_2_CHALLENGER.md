# DF-CFG-2 / US-DF-CFG-2 research registration

Registered 2026-11-04, before any market-data evaluation of these configs.
These are opt-in research challengers, not a release or promotion. Existing
DF-CFG-1 / US-DF-CFG-1 arms, QuantEdge and frozen forecasts remain unchanged.
SIG-v1 acceptance and deployment remain a separate release task.

## Confirmed mechanism and correction

The legacy positive signed-factor IC gate admits an unconstrained quintile-return
table. A positive global IC does not guarantee ordered bin means: the synthetic
regression table [0, 3, 1, 4, 5] passes the gate but reverses its middle bins.
This validates a possible direction inconsistency, not the review's numerical
claims about observed market performance or actual volatility exposure.

The new DirectionalQuantEdge fits the gate and table within each training fold,
using only rows before the cutoff whose h3 labels matured strictly before it.
Weighted pool-adjacent-violators regression makes the five bin means increasing
in the signed factor percentile. Negative/unknown IC disables the group; it
does not reverse its sign. Missing bins disable a group. All features in every
active group must be finite for a row to receive a quant score. Nonfinite labels
are excluded. Each fold records IC, bin counts, cutoff and fitted table.

Risk inputs keep their negative signs. Holding other factor values fixed,
higher vol20/atr_pct cannot improve the risk contribution. This is a structural
property, not a claim that the combined score has zero empirical volatility
correlation: correlated momentum and other factors can still produce exposure.
Component-level OOS attribution and empirical volatility loading remain pending.

## Fixed comparison and evaluation gate

Each market has exactly one new arm: DF-Q2 or US-DF-Q2, technical plus corrected
quant at equal relative weights (existing technical/quant weights preserved).
The comparator is that market's DF0. There is no weight search, learned-weight
arm, fundamental component, confidence challenger or live registration.
This isolates the direction correction before adding other components.

Primary endpoint per market: mean paired per-session h3 rank-IC difference
against DF0 on common valid sessions, using the existing chronological folds,
label embargo and liquidity requirements. Positive direction is required.
HAC and block-bootstrap inference must both support the result. The two market
primary tests form one family: Holm adjustment at family alpha 0.05 is required
before citing selection evidence (implementation/evidence pending). Secondary
returns, tail capture, regimes and component diagnostics remain exploratory.
No existing SIG flag authorizes promotion. No proven edge is asserted.

Require at least 50 paired OOS sessions, independent retest across regimes,
cost-aware trade construction and valid Paper/Shadow lifecycle evidence before
any promotion proposal. Retrospective data are development evidence; immutable
forward snapshots collected after registration are needed for confirmation.
US historical membership/survivorship limitations must remain explicit.

## Reproducible opt-in entry point

Append `--quant-challenger` to the existing `python -m app.learning.fusion_run`
command with authorized scratch `--state` and the usual market/data arguments.
It writes `<state>/<market>/fusion_walkforward_cfg2.json`, never the default
`fusion_walkforward.json`. Nightly schedules and live consumers are unchanged.
No old predictions or labels are rewritten or rescored by this implementation.

Validation uses synthetic fixtures only: weighted monotonic fit, signed risk
monotonicity, disabling negative gates, missing/nonfinite truth, strict cutoff,
EGX/US baseline equality, unchanged legacy execution after challenger evaluation,
and cache isolation. Full-scale market evaluation and independent acceptance
are pending. LIVE_MONEY=DISABLED.

Final affected-suite result: **75 passed, 85 subtests passed in 27.83s**.
`git diff --check` passed. No full release suite or runtime scan was run.
