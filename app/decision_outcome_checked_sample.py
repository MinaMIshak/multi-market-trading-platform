"""EGX sample assessment over only outcomes attested CLEAR by ``as_of`` (Phase H/I/K).

decision-outcome-checked-sample-assessment-v1 composes two existing pure
components without changing either:

1. ``select_egx_checked_outcomes`` (decision-outcome-checked-metrics-v1):
   integrity gate over ALL supplied outcomes, re-verified attestation
   partition and rule CHECK_AS_OF_AT_OR_BEFORE_AS_OF_V1. Only outcomes
   attested CLEAR with ``check_as_of`` at or before ``as_of`` are selected;
2. ``assess_decision_outcome_sample`` (decision-outcome-sample-assessment-v1)
   with the SAME full population and only the selected outcomes, under the
   caller's explicit versioned EGX sample policy (revalidated by that
   component: exact type, market, registration not after ``as_of``).

So in every strategy id/version/config segment an UNATTESTED,
ATTESTATION_INVALID, later-attested or missing outcome stays in the sample as
NOT_EVALUATED: it is not a CLOSED outcome and it counts as missing data. An
unchecked outcome can therefore never help a segment reach the declared
minimum, and restricting to checked outcomes cannot hide a loss or shrink a
segment's sample. Attestation counts are reported for the whole input only;
per-segment attestation counts are NOT_REPORTED (the segment NOT_EVALUATED
counts include them without a reason breakdown).

The policy thresholds stay DECLARED_UNVALIDATED: meeting them is not
statistical significance, not empirical validation and never promotion. No
repository policy instance exists (gap SAMPLE_ASSESSMENT_POLICY_NOT_REGISTERED
stays open). No cross-segment headline. No IO, store, job, route or
scheduler; nothing runs this module. Outcomes are hypothetical results from
session bars: not fills, not performance. PAPER/SHADOW only; LIVE_MONEY=false.
"""
from app.decision_observation import _utc
from app.decision_outcome_checked_metrics import (
    ATTESTATION_TIME_RULE, CONTRACT_VERSION as CHECKED_VERSION, select_egx_checked_outcomes)
from app.decision_outcome_sample import (
    CONTRACT_VERSION as SAMPLE_VERSION, assess_decision_outcome_sample)

CONTRACT_VERSION = 'decision-outcome-checked-sample-assessment-v1'


def assess_egx_checked_decision_outcome_sample(population, outcomes, attestations, *,
                                               assumptions, as_of, policy) -> dict:
    """JSON-ready decision-outcome-checked-sample-assessment-v1 for EGX; pure, fails closed.

    Inputs are as for ``aggregate_egx_checked_decision_outcomes`` plus an exact
    EGX sample policy (revalidated by the sample component). Raises
    ``ValueError`` on any integrity or policy failure (nothing is silently
    dropped). Inputs are not mutated.
    """
    selection = select_egx_checked_outcomes(population, outcomes, attestations,
                                            assumptions=assumptions, as_of=as_of)
    partition, counted = selection['partition'], selection['counted']
    assessment = assess_decision_outcome_sample(
        population, counted, market='EGX', assumptions=assumptions, as_of=selection['as_of'],
        policy=policy)
    return {
        'contract_version': CONTRACT_VERSION,
        'checked_selection_version': CHECKED_VERSION,
        'sample_assessment_version': SAMPLE_VERSION,
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
        'per_segment_attestation_counts': 'NOT_REPORTED',
        'partition': partition,
        'sample_assessment_report': assessment,
        'unrestricted_assessment': 'NOT_REPORTED',
        'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR',
        'absence_is_proof': False,
        'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'attestation_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'attestation_retention_time': 'NOT_RECORDED_NOT_CHECKED',
        'check_recomputation': 'NOT_PERFORMED',
        'review_verification': 'CALLER_ASSERTED_UNVERIFIED',
        'price_adjustment': 'NOT_APPLIED',
        'sample_assessment': 'ASSESSED_AGAINST_DECLARED_UNVALIDATED_POLICY',
        'statistical_significance': 'NOT_ASSESSED',
        'empirical_validation': 'NOT_VALIDATED',
        'cross_segment_headline': 'NOT_COMPUTED',
        'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED',
    }
