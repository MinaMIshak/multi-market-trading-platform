"""Per-period EGX metrics over only outcomes attested CLEAR by ``as_of`` (Phase I/J/K).

decision-outcome-checked-period-metrics-v1 composes two existing pure
components without changing either:

1. ``select_egx_checked_outcomes`` (decision-outcome-checked-metrics-v1):
   integrity gate over ALL supplied outcomes, re-verified attestation
   partition and rule CHECK_AS_OF_AT_OR_BEFORE_AS_OF_V1. Only outcomes
   attested CLEAR with ``check_as_of`` at or before ``as_of`` are selected;
2. ``aggregate_decision_outcomes_by_period`` (decision-outcome-period-metrics-v1)
   with the SAME full population and only the selected outcomes, under the
   caller's explicit versioned ``EvaluationPeriodPolicy``.

So inside every period (and in UNASSIGNED) an UNATTESTED, ATTESTATION_INVALID,
later-attested or missing outcome stays in the sample as NOT_EVALUATED; it can
neither hide a loss nor shrink a period's sample. Period assignment still uses
only each observation's ``detected_at``, never an outcome or attestation time.
Attestation counts are reported for the whole input only; per-period
attestation counts are NOT_REPORTED (the period NOT_EVALUATED counts include
them without a reason breakdown).

No cross-period headline, no sample assessment, no repository policy instance
(gap EVALUATION_PERIOD_POLICY_NOT_REGISTERED stays open). No IO, store, job,
route or scheduler; nothing runs this module. Outcomes are hypothetical results
from session bars: not fills, not performance. PAPER/SHADOW only;
LIVE_MONEY=false.
"""
from app.decision_observation import _utc
from app.decision_outcome_checked_metrics import (
    ATTESTATION_TIME_RULE, CONTRACT_VERSION as CHECKED_VERSION, select_egx_checked_outcomes)
from app.decision_outcome_periods import (
    CONTRACT_VERSION as PERIODS_VERSION, aggregate_decision_outcomes_by_period)

CONTRACT_VERSION = 'decision-outcome-checked-period-metrics-v1'


def aggregate_egx_checked_decision_outcomes_by_period(population, outcomes, attestations, *,
                                                      assumptions, as_of, policy) -> dict:
    """JSON-ready decision-outcome-checked-period-metrics-v1 for EGX; pure, fails closed.

    Inputs are as for ``aggregate_egx_checked_decision_outcomes`` plus an exact
    EGX evaluation-period policy (validated by the period component). Raises ``ValueError`` on any integrity or
    policy failure (nothing is silently dropped). Inputs are not mutated.
    """
    selection = select_egx_checked_outcomes(population, outcomes, attestations,
                                            assumptions=assumptions, as_of=as_of)
    partition, counted = selection['partition'], selection['counted']
    by_period = aggregate_decision_outcomes_by_period(
        population, counted, market='EGX', assumptions=assumptions, as_of=selection['as_of'],
        policy=policy)
    return {
        'contract_version': CONTRACT_VERSION,
        'checked_selection_version': CHECKED_VERSION,
        'period_metrics_version': PERIODS_VERSION,
        'market': 'EGX',
        'as_of': _utc(selection['as_of']),
        'outcome_basis': 'ATTESTED_CLEAR_ONLY',
        'attestation_time_rule': ATTESTATION_TIME_RULE,
        'outcomes_supplied': partition['outcomes_supplied'],
        'attestations_supplied': partition['attestations_supplied'],
        'attestation_counts': selection['counts'],
        'outcomes_counted': len(counted),
        'outcomes_not_counted': partition['outcomes_supplied'] - len(counted),
        'not_counted_treatment': 'NOT_EVALUATED_IN_SAME_POPULATION_NEVER_DROPPED',
        'attested_after_as_of': selection['after'],
        'per_period_attestation_counts': 'NOT_REPORTED',
        'partition': partition,
        'period_metrics': by_period,
        'unrestricted_metrics': 'NOT_REPORTED',
        'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR',
        'absence_is_proof': False,
        'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'attestation_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'attestation_retention_time': 'NOT_RECORDED_NOT_CHECKED',
        'check_recomputation': 'NOT_PERFORMED',
        'review_verification': 'CALLER_ASSERTED_UNVERIFIED',
        'holdout_integrity': 'CALLER_ASSERTED_UNVERIFIED',
        'price_adjustment': 'NOT_APPLIED',
        'sample_assessment': 'NOT_ASSESSED',
        'cross_period_headline': 'NOT_COMPUTED',
        'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED',
    }
