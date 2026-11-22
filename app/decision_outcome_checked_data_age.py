"""Per-data-age EGX metrics over only outcomes attested CLEAR by ``as_of`` (Phase I/K).

decision-outcome-checked-data-age-metrics-v1 composes two existing pure
components without changing either:

1. ``select_egx_checked_outcomes`` (decision-outcome-checked-metrics-v1):
   integrity gate over ALL supplied outcomes, re-verified attestation
   partition and rule CHECK_AS_OF_AT_OR_BEFORE_AS_OF_V1. Only outcomes
   attested CLEAR with ``check_as_of`` at or before ``as_of`` are selected;
2. ``aggregate_decision_outcomes_by_data_age``
   (decision-outcome-data-age-metrics-v1) with the SAME full population and
   only the selected outcomes, under the caller's explicit versioned EGX
   data-age bucket policy (validated by that component).

So inside every age bucket an UNATTESTED, ATTESTATION_INVALID, later-attested
or missing outcome stays in the sample as NOT_EVALUATED; it can neither hide a
loss nor shrink a bucket's sample. Bucket assignment still uses only each
retained observation's ``detected_at - data_as_of`` (wall clock, not
session-aware), never an outcome or attestation time. Attestation counts are
reported for the whole input only; per-bucket attestation counts are
NOT_REPORTED (the bucket NOT_EVALUATED counts include them without a reason
breakdown).

Bucket bounds are declared, not validated; no age is interpreted as acceptable
or risky, and nothing is accepted, rejected or ranked by age. No cross-bucket
headline, no sample assessment, no repository policy instance (gap
DATA_AGE_POLICY_NOT_REGISTERED stays open). No IO, store, job, route or
scheduler; nothing runs this module. Outcomes are hypothetical results from
session bars: not fills, not performance. PAPER/SHADOW only; LIVE_MONEY=false.
"""
from app.decision_observation import _utc
from app.decision_outcome_checked_metrics import (
    ATTESTATION_TIME_RULE, CONTRACT_VERSION as CHECKED_VERSION, select_egx_checked_outcomes)
from app.decision_outcome_data_age import (
    CONTRACT_VERSION as DATA_AGE_VERSION, aggregate_decision_outcomes_by_data_age)

CONTRACT_VERSION = 'decision-outcome-checked-data-age-metrics-v1'


def aggregate_egx_checked_decision_outcomes_by_data_age(population, outcomes, attestations, *,
                                                        assumptions, as_of, policy) -> dict:
    """JSON-ready decision-outcome-checked-data-age-metrics-v1 for EGX; pure, fails closed.

    Inputs are as for ``aggregate_egx_checked_decision_outcomes`` plus an exact
    EGX data-age policy (validated by the data-age component). Raises
    ``ValueError`` on any integrity, source-time or policy failure (nothing is
    silently dropped). Inputs are not mutated.
    """
    selection = select_egx_checked_outcomes(population, outcomes, attestations,
                                            assumptions=assumptions, as_of=as_of)
    partition, counted = selection['partition'], selection['counted']
    by_age = aggregate_decision_outcomes_by_data_age(
        population, counted, market='EGX', assumptions=assumptions, as_of=selection['as_of'],
        policy=policy)
    return {
        'contract_version': CONTRACT_VERSION,
        'checked_selection_version': CHECKED_VERSION,
        'data_age_metrics_version': DATA_AGE_VERSION,
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
        'per_bucket_attestation_counts': 'NOT_REPORTED',
        'partition': partition,
        'data_age_metrics': by_age,
        'unrestricted_metrics': 'NOT_REPORTED',
        'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR',
        'absence_is_proof': False,
        'age_interpretation': 'NOT_EVALUATED',
        'trading_effect': 'NONE',
        'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'attestation_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'attestation_retention_time': 'NOT_RECORDED_NOT_CHECKED',
        'check_recomputation': 'NOT_PERFORMED',
        'review_verification': 'CALLER_ASSERTED_UNVERIFIED',
        'price_adjustment': 'NOT_APPLIED',
        'sample_assessment': 'NOT_ASSESSED',
        'cross_bucket_headline': 'NOT_COMPUTED',
        'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED',
    }
