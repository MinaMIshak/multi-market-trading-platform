"""decision-outcome-checked-sample-assessment-v1: sample assessment over attested-CLEAR outcomes."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json

import pytest

from app.decision_observation import canonical_bytes
from app.decision_observation_store import _time
from app.decision_outcome_action_attestation import attest_outcome_corporate_actions
from app.decision_outcome_checked_metrics import (
    ATTESTATION_TIME_RULE, COUNT_KEYS, aggregate_egx_checked_decision_outcomes)
from app.decision_outcome_checked_sample import (
    CONTRACT_VERSION, assess_egx_checked_decision_outcome_sample)
from app.decision_outcome_metrics import NOT_EVALUATED
from app.decision_outcome_sample import (
    BELOW, CLOSED_BELOW_MINIMUM, MEETS, MISSING_ABOVE_MAXIMUM, SampleAssessmentPolicy,
    assess_decision_outcome_sample)
from tests.test_decision_outcome_action_attestation import checked, report  # noqa: F401
from tests.test_decision_outcome_producer import NOW
from tests.test_decision_outcome_session_bars import ZERO

LATER = NOW + timedelta(days=1)
SAFETY = {'outcome_basis': 'ATTESTED_CLEAR_ONLY',
          'attestation_time_rule': ATTESTATION_TIME_RULE,
          'checked_selection_version': 'decision-outcome-checked-metrics-v1',
          'sample_assessment_version': 'decision-outcome-sample-assessment-v1',
          'not_counted_treatment': 'NOT_EVALUATED_IN_SAME_POPULATION_NEVER_DROPPED',
          'per_segment_attestation_counts': 'NOT_REPORTED',
          'unrestricted_assessment': 'NOT_REPORTED',
          'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR', 'absence_is_proof': False,
          'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
          'attestation_completeness': 'CALLER_ASSERTED_UNVERIFIED',
          'attestation_retention_time': 'NOT_RECORDED_NOT_CHECKED',
          'check_recomputation': 'NOT_PERFORMED',
          'review_verification': 'CALLER_ASSERTED_UNVERIFIED', 'price_adjustment': 'NOT_APPLIED',
          'sample_assessment': 'ASSESSED_AGAINST_DECLARED_UNVALIDATED_POLICY',
          'statistical_significance': 'NOT_ASSESSED', 'empirical_validation': 'NOT_VALIDATED',
          'cross_segment_headline': 'NOT_COMPUTED',
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


def policy(observation, *, market='EGX', registered_at=None):
    """Minimum one CLOSED outcome and no missing data; registered before detection."""
    detected = _time(observation[1]['detected_at'], 'detected_at')
    return SampleAssessmentPolicy(
        policy_version='fixture-checked-sample-1', market=market,
        registered_at=detected - timedelta(days=2) if registered_at is None else registered_at,
        min_closed_outcomes=1, max_missing_data_rate=Decimal('0'))


def run(observation, outcomes, attestations, as_of=NOW, rules=None):
    return assess_egx_checked_decision_outcome_sample(
        (observation,), outcomes, attestations, assumptions=ZERO, as_of=as_of,
        policy=policy(observation) if rules is None else rules)


def counts(**values):
    return {key: values.get(key, 0) for key in COUNT_KEYS}


def only_segment(result):
    (segment,) = result['sample_assessment_report']['segments']
    return segment


def test_attested_clear_outcome_counts_toward_the_declared_minimum(checked):
    outcome_identity, outcome, observation, att = parts(checked)
    rules = policy(observation)
    out = run(observation, (outcome,), (att,), rules=rules)
    assert out['contract_version'] == CONTRACT_VERSION == (
        'decision-outcome-checked-sample-assessment-v1')
    assert out['market'] == 'EGX' and out['as_of'] == att['check_as_of']
    assert out['attestation_counts'] == counts(ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF=1)
    assert (out['outcomes_supplied'], out['attestations_supplied']) == (1, 1)
    assert (out['outcomes_counted'], out['outcomes_not_counted']) == (1, 0)
    assert out['attested_after_as_of'] == []
    # The embedded assessment is exactly the unchanged sample component's output
    # over the same population and the counted outcomes.
    assert out['sample_assessment_report'] == assess_decision_outcome_sample(
        (observation,), (outcome,), market='EGX', assumptions=ZERO, as_of=NOW, policy=rules)
    # The selection is exactly the checked-metrics component's.
    checked_report = aggregate_egx_checked_decision_outcomes(
        (observation,), (outcome,), (att,), assumptions=ZERO, as_of=NOW)
    assert out['partition'] == checked_report['partition']
    assert out['attestation_counts'] == checked_report['attestation_counts']
    segment = only_segment(out)
    assert (segment['observations'], segment['closed']) == (1, 1)
    assert segment['state'] == MEETS and segment['reasons'] == []
    assert segment['registration'] == 'REGISTERED_AT_OR_BEFORE_FIRST_DETECTION'
    assert out['sample_assessment_report']['segment_state_counts'] == {MEETS: 1, BELOW: 0}
    assert not {'segments', 'metrics', 'hit_rate', 'expectancy_net_r', 'policy'} & set(out)
    assert {key: out[key] for key in SAFETY} == SAFETY
    assert out['partition']['entries'][0]['outcome_identity'] == outcome_identity
    json.dumps(out, allow_nan=False)


def test_unattested_outcome_cannot_help_a_segment_meet_the_minimum(checked):
    _, outcome, observation, _ = parts(checked)
    rules = policy(observation)
    # Unrestricted, the unchecked CLOSED outcome would meet the declared minimum ...
    unrestricted = assess_decision_outcome_sample(
        (observation,), (outcome,), market='EGX', assumptions=ZERO, as_of=NOW, policy=rules)
    assert unrestricted['segments'][0]['state'] == MEETS
    # ... but checked, it stays in the sample as NOT_EVALUATED (missing data).
    out = run(observation, (outcome,), (), rules=rules)
    assert out['attestation_counts'] == counts(UNATTESTED=1)
    assert (out['outcomes_counted'], out['outcomes_not_counted']) == (0, 1)
    segment = only_segment(out)
    assert (segment['observations'], segment['closed']) == (1, 0)
    assert segment['missing_data_rate'] == '1'
    assert segment['state'] == BELOW
    assert segment['reasons'] == [CLOSED_BELOW_MINIMUM, MISSING_ABOVE_MAXIMUM]
    (metrics_segment,) = out['sample_assessment_report']['metrics']['segments']
    assert metrics_segment['status_counts'][NOT_EVALUATED] == 1


def test_invalid_attestation_is_not_evaluated_and_below_minimum(checked):
    _, outcome, observation, att = parts(checked)
    forged = deepcopy(att)
    forged['strategy_version'] = 'forged'
    out = run(observation, (outcome,), (forged,))
    assert out['attestation_counts'] == counts(ATTESTATION_INVALID=1)
    assert only_segment(out)['state'] == BELOW
    assert only_segment(out)['closed'] == 0


def test_later_attestation_cannot_leak_into_an_earlier_assessment(checked):
    outcome_identity, outcome, observation, _ = checked
    later = attest_outcome_corporate_actions(
        outcome_identity, outcome, observation, report(observation, as_of=LATER))
    early = run(observation, (outcome,), (later,), as_of=NOW)
    assert early['attestation_counts'] == counts(ATTESTED_CLEAR_AFTER_AS_OF=1)
    assert early['attested_after_as_of'] == [outcome_identity]
    assert only_segment(early)['state'] == BELOW
    current = run(observation, (outcome,), (later,), as_of=LATER)
    assert current['attestation_counts'] == counts(ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF=1)
    assert only_segment(current)['state'] == MEETS


def test_policy_and_integrity_failures_fail_closed(checked):
    _, outcome, observation, att = parts(checked)
    with pytest.raises(ValueError, match='market mismatch'):
        run(observation, (outcome,), (att,), rules=policy(observation, market='US'))
    with pytest.raises(ValueError, match='SampleAssessmentPolicy'):
        run(observation, (outcome,), (att,), rules=policy(observation).model_dump())
    with pytest.raises(ValueError, match='registered after as_of'):
        run(observation, (outcome,), (att,),
            rules=policy(observation, registered_at=NOW + timedelta(seconds=1)))
    # The unchanged checked selection gate runs first over everything supplied.
    with pytest.raises(ValueError, match='as_of'):
        run(observation, (outcome,), (att,), as_of=NOW - timedelta(minutes=1))
    with pytest.raises(ValueError, match='more than one outcome'):
        run(observation, (outcome, outcome), ())
    with pytest.raises(ValueError, match='timezone-aware'):
        run(observation, (outcome,), (att,), as_of=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match='OutcomeAssumptions'):
        assess_egx_checked_decision_outcome_sample(
            (observation,), (outcome,), (att,), assumptions=ZERO.model_dump(), as_of=NOW,
            policy=policy(observation))


def test_registration_after_first_detection_stays_visible(checked):
    _, outcome, observation, att = parts(checked)
    detected = _time(observation[1]['detected_at'], 'detected_at')
    late = policy(observation, registered_at=detected + timedelta(seconds=1))
    assert late.registered_at <= NOW
    assert only_segment(run(observation, (outcome,), (att,), rules=late))['registration'] == (
        'REGISTERED_AFTER_FIRST_DETECTION')


def test_pure_deterministic_and_inputs_not_mutated(checked):
    _, outcome, observation, att = parts(checked)
    rules = policy(observation)
    before = deepcopy((observation, outcome, att, rules.model_dump(mode='json')))
    first = run(observation, (outcome,), (att,), rules=rules)
    second = run(observation, (outcome,), (att,), rules=rules)
    assert first == second and canonical_bytes(first) == canonical_bytes(second)
    assert (observation, outcome, att, rules.model_dump(mode='json')) == before
