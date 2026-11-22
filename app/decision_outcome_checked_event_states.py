"""Per-event-state EGX metrics over only outcomes attested CLEAR by ``as_of`` (Phase I/K).

decision-outcome-checked-event-state-metrics-v1 composes two existing pure
components without changing either:

1. ``select_egx_checked_outcomes`` (decision-outcome-checked-metrics-v1):
   integrity gate over ALL supplied outcomes, re-verified attestation
   partition and rule CHECK_AS_OF_AT_OR_BEFORE_AS_OF_V1. Only outcomes
   attested CLEAR with ``check_as_of`` at or before ``as_of`` are selected;
2. ``aggregate_decision_outcomes_by_event_state``
   (decision-outcome-event-state-metrics-v1) with the SAME full population and
   only the selected outcomes.

So inside every event state an UNATTESTED, ATTESTATION_INVALID, later-attested
or missing outcome stays in the sample as NOT_EVALUATED; it can neither hide a
loss nor shrink a state's sample. State assignment still uses only the
event-context section retained inside each observation at its detection time,
never an outcome or attestation time. Attestation counts are reported for the
whole input only; per-state attestation counts are NOT_REPORTED (the state
NOT_EVALUATED counts include them without a reason breakdown).

The event state is inference, not an observed market fact; NO_EVENT_FOUND is
not proof that no event existed, and no state is interpreted as risk or used to
accept, reject or rank anything. No cross-state headline, no sample assessment.
No IO, store, job, route or scheduler; nothing runs this module. Outcomes are
hypothetical results from session bars: not fills, not performance.
PAPER/SHADOW only; LIVE_MONEY=false.
"""
from app.decision_observation import _utc
from app.decision_outcome_checked_metrics import (
    ATTESTATION_TIME_RULE, CONTRACT_VERSION as CHECKED_VERSION, select_egx_checked_outcomes)
from app.decision_outcome_event_states import (
    CONTRACT_VERSION as EVENT_STATES_VERSION, aggregate_decision_outcomes_by_event_state)

CONTRACT_VERSION = 'decision-outcome-checked-event-state-metrics-v1'


def aggregate_egx_checked_decision_outcomes_by_event_state(population, outcomes, attestations, *,
                                                           assumptions, as_of) -> dict:
    """JSON-ready decision-outcome-checked-event-state-metrics-v1 for EGX; pure, fails closed.

    Inputs are as for ``aggregate_egx_checked_decision_outcomes``. Raises
    ``ValueError`` on any integrity failure, including an attached event-context
    section the event-state component refuses (nothing is silently dropped).
    Inputs are not mutated.
    """
    selection = select_egx_checked_outcomes(population, outcomes, attestations,
                                            assumptions=assumptions, as_of=as_of)
    partition, counted = selection['partition'], selection['counted']
    by_state = aggregate_decision_outcomes_by_event_state(
        population, counted, market='EGX', assumptions=assumptions, as_of=selection['as_of'])
    return {
        'contract_version': CONTRACT_VERSION,
        'checked_selection_version': CHECKED_VERSION,
        'event_state_metrics_version': EVENT_STATES_VERSION,
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
        'per_state_attestation_counts': 'NOT_REPORTED',
        'partition': partition,
        'event_state_metrics': by_state,
        'unrestricted_metrics': 'NOT_REPORTED',
        'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR',
        'absence_is_proof': False,
        'evidence_layer': 'NEWS_EVENT_INFERENCE',
        'risk_interpretation': 'NOT_EVALUATED',
        'trading_effect': 'NONE',
        'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'attestation_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'attestation_retention_time': 'NOT_RECORDED_NOT_CHECKED',
        'check_recomputation': 'NOT_PERFORMED',
        'review_verification': 'CALLER_ASSERTED_UNVERIFIED',
        'price_adjustment': 'NOT_APPLIED',
        'sample_assessment': 'NOT_ASSESSED',
        'cross_state_headline': 'NOT_COMPUTED',
        'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED',
    }
