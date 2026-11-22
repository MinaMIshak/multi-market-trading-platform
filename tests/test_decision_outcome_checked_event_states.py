"""decision-outcome-checked-event-state-metrics-v1: per-event-state metrics over attested-CLEAR outcomes."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from app.decision_observation import canonical_bytes
from app.decision_outcome_action_attestation import attest_outcome_corporate_actions
from app.decision_outcome_checked_event_states import (
    CONTRACT_VERSION, aggregate_egx_checked_decision_outcomes_by_event_state)
from app.decision_outcome_checked_metrics import (
    ATTESTATION_TIME_RULE, COUNT_KEYS, aggregate_egx_checked_decision_outcomes)
from app.decision_outcome_event_states import (
    EVENT_STATES, NOT_ATTACHED, aggregate_decision_outcomes_by_event_state)
from app.decision_outcome_metrics import NOT_EVALUATED
from tests.test_decision_outcome_action_attestation import checked, report  # noqa: F401
from tests.test_decision_outcome_producer import NOW
from tests.test_decision_outcome_session_bars import ZERO

LATER = NOW + timedelta(days=1)
SAFETY = {'outcome_basis': 'ATTESTED_CLEAR_ONLY',
          'attestation_time_rule': ATTESTATION_TIME_RULE,
          'checked_selection_version': 'decision-outcome-checked-metrics-v1',
          'event_state_metrics_version': 'decision-outcome-event-state-metrics-v1',
          'not_counted_treatment': 'NOT_EVALUATED_IN_SAME_POPULATION_NEVER_DROPPED',
          'per_state_attestation_counts': 'NOT_REPORTED',
          'unrestricted_metrics': 'NOT_REPORTED',
          'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR', 'absence_is_proof': False,
          'evidence_layer': 'NEWS_EVENT_INFERENCE', 'risk_interpretation': 'NOT_EVALUATED',
          'trading_effect': 'NONE',
          'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
          'attestation_completeness': 'CALLER_ASSERTED_UNVERIFIED',
          'attestation_retention_time': 'NOT_RECORDED_NOT_CHECKED',
          'check_recomputation': 'NOT_PERFORMED',
          'review_verification': 'CALLER_ASSERTED_UNVERIFIED', 'price_adjustment': 'NOT_APPLIED',
          'sample_assessment': 'NOT_ASSESSED', 'cross_state_headline': 'NOT_COMPUTED',
          'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
          'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
          'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The stores refuse group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


def parts(checked):
    outcome_identity, outcome, observation, clear = checked
    attestation = attest_outcome_corporate_actions(outcome_identity, outcome, observation, clear)
    return outcome_identity, outcome, observation, attestation


def run(observation, outcomes, attestations, as_of=NOW):
    return aggregate_egx_checked_decision_outcomes_by_event_state(
        (observation,), outcomes, attestations, assumptions=ZERO, as_of=as_of)


def counts(**values):
    return {key: values.get(key, 0) for key in COUNT_KEYS}


def state(result, name):
    (found,) = [s for s in result['event_state_metrics']['states'] if s['event_state'] == name]
    return found


def only_segment(result, name=NOT_ATTACHED):
    (segment,) = state(result, name)['metrics']['segments']
    return segment


def test_attested_clear_outcome_is_counted_in_its_event_state(checked):
    outcome_identity, outcome, observation, att = parts(checked)
    # The producer retains observations without event context.
    assert observation[1]['event_context']['status'] == NOT_ATTACHED
    out = run(observation, (outcome,), (att,))
    assert out['contract_version'] == CONTRACT_VERSION == (
        'decision-outcome-checked-event-state-metrics-v1')
    assert out['market'] == 'EGX' and out['as_of'] == att['check_as_of']
    assert out['attestation_counts'] == counts(ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF=1)
    assert (out['outcomes_supplied'], out['attestations_supplied']) == (1, 1)
    assert (out['outcomes_counted'], out['outcomes_not_counted']) == (1, 0)
    assert out['attested_after_as_of'] == []
    # Embedded state metrics are exactly the unchanged event-state component's
    # output over the same population and the counted outcomes.
    assert out['event_state_metrics'] == aggregate_decision_outcomes_by_event_state(
        (observation,), (outcome,), market='EGX', assumptions=ZERO, as_of=NOW)
    # The selection is exactly the checked-metrics component's.
    checked_report = aggregate_egx_checked_decision_outcomes(
        (observation,), (outcome,), (att,), assumptions=ZERO, as_of=NOW)
    assert out['partition'] == checked_report['partition']
    assert out['attestation_counts'] == checked_report['attestation_counts']
    segment = only_segment(out)
    assert segment['observations'] == 1 and segment['status_counts']['CLOSED'] == 1
    # Every state is still reported; the others are empty.
    by_state = out['event_state_metrics']
    assert [s['event_state'] for s in by_state['states']] == list(EVENT_STATES)
    assert by_state['state_counts'] == {
        name: (1 if name == NOT_ATTACHED else 0) for name in EVENT_STATES}
    assert by_state['cross_state_headline'] == 'NOT_COMPUTED'
    assert not {'segments', 'hit_rate', 'expectancy_net_r', 'metrics', 'states'} & set(out)
    assert {key: out[key] for key in SAFETY} == SAFETY
    assert out['partition']['entries'][0]['outcome_identity'] == outcome_identity
    json.dumps(out, allow_nan=False)


def test_unattested_outcome_is_not_evaluated_inside_its_event_state(checked):
    _, outcome, observation, _ = parts(checked)
    out = run(observation, (outcome,), ())
    assert out['attestation_counts'] == counts(UNATTESTED=1)
    assert (out['outcomes_counted'], out['outcomes_not_counted']) == (0, 1)
    assert state(out, NOT_ATTACHED)['observations'] == 1
    segment = only_segment(out)
    # The unchecked (CLOSED) outcome stays in the state's sample as NOT_EVALUATED.
    assert segment['observations'] == 1 and segment['closed'] == 0
    assert segment['status_counts'][NOT_EVALUATED] == 1
    assert segment['missing_data_rate'] == '1'


def test_invalid_attestation_is_not_evaluated_inside_its_event_state(checked):
    _, outcome, observation, att = parts(checked)
    forged = deepcopy(att)
    forged['strategy_version'] = 'forged'
    out = run(observation, (outcome,), (forged,))
    assert out['attestation_counts'] == counts(ATTESTATION_INVALID=1)
    assert only_segment(out)['status_counts'][NOT_EVALUATED] == 1


def test_later_attestation_cannot_leak_into_an_earlier_event_state_report(checked):
    outcome_identity, outcome, observation, _ = checked
    later = attest_outcome_corporate_actions(
        outcome_identity, outcome, observation, report(observation, as_of=LATER))
    early = run(observation, (outcome,), (later,), as_of=NOW)
    assert early['attestation_counts'] == counts(ATTESTED_CLEAR_AFTER_AS_OF=1)
    assert early['attested_after_as_of'] == [outcome_identity]
    assert only_segment(early)['status_counts'][NOT_EVALUATED] == 1
    current = run(observation, (outcome,), (later,), as_of=LATER)
    assert current['attestation_counts'] == counts(ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF=1)
    assert only_segment(current)['status_counts']['CLOSED'] == 1


def test_integrity_failures_fail_closed(checked):
    _, outcome, observation, att = parts(checked)
    # The unchanged checked selection gate runs first over everything supplied.
    with pytest.raises(ValueError, match='as_of'):
        run(observation, (outcome,), (att,), as_of=NOW - timedelta(minutes=1))
    with pytest.raises(ValueError, match='more than one outcome'):
        run(observation, (outcome, outcome), ())
    with pytest.raises(ValueError, match='timezone-aware'):
        run(observation, (outcome,), (att,), as_of=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match='OutcomeAssumptions'):
        aggregate_egx_checked_decision_outcomes_by_event_state(
            (observation,), (outcome,), (att,), assumptions=ZERO.model_dump(), as_of=NOW)


def test_pure_deterministic_and_inputs_not_mutated(checked):
    _, outcome, observation, att = parts(checked)
    before = deepcopy((observation, outcome, att))
    first = run(observation, (outcome,), (att,))
    second = run(observation, (outcome,), (att,))
    assert first == second and canonical_bytes(first) == canonical_bytes(second)
    assert (observation, outcome, att) == before
