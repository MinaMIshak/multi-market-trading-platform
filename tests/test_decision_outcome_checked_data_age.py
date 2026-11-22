"""decision-outcome-checked-data-age-metrics-v1: per-data-age metrics over attested-CLEAR outcomes."""
from copy import deepcopy
from datetime import timedelta
import json
import math

import pytest

from app.decision_observation import canonical_bytes
from app.decision_observation_store import _time
from app.decision_outcome_action_attestation import attest_outcome_corporate_actions
from app.decision_outcome_checked_data_age import (
    CONTRACT_VERSION, aggregate_egx_checked_decision_outcomes_by_data_age)
from app.decision_outcome_checked_metrics import (
    ATTESTATION_TIME_RULE, COUNT_KEYS, aggregate_egx_checked_decision_outcomes)
from app.decision_outcome_data_age import (
    MAX_BOUND_SECONDS, DataAgePolicy, aggregate_decision_outcomes_by_data_age, bucket_labels)
from app.decision_outcome_metrics import NOT_EVALUATED
from tests.test_decision_outcome_action_attestation import checked, report  # noqa: F401
from tests.test_decision_outcome_producer import NOW
from tests.test_decision_outcome_session_bars import ZERO

LATER = NOW + timedelta(days=1)
SAFETY = {'outcome_basis': 'ATTESTED_CLEAR_ONLY',
          'attestation_time_rule': ATTESTATION_TIME_RULE,
          'checked_selection_version': 'decision-outcome-checked-metrics-v1',
          'data_age_metrics_version': 'decision-outcome-data-age-metrics-v1',
          'not_counted_treatment': 'NOT_EVALUATED_IN_SAME_POPULATION_NEVER_DROPPED',
          'per_bucket_attestation_counts': 'NOT_REPORTED',
          'unrestricted_metrics': 'NOT_REPORTED',
          'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR', 'absence_is_proof': False,
          'age_interpretation': 'NOT_EVALUATED', 'trading_effect': 'NONE',
          'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
          'attestation_completeness': 'CALLER_ASSERTED_UNVERIFIED',
          'attestation_retention_time': 'NOT_RECORDED_NOT_CHECKED',
          'check_recomputation': 'NOT_PERFORMED',
          'review_verification': 'CALLER_ASSERTED_UNVERIFIED', 'price_adjustment': 'NOT_APPLIED',
          'sample_assessment': 'NOT_ASSESSED', 'cross_bucket_headline': 'NOT_COMPUTED',
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


def age_seconds(observation):
    """Ceiling of the retained detected_at - data_as_of, in whole seconds."""
    record = observation[1]
    age = _time(record['detected_at'], 'detected_at') - _time(record['data_as_of'], 'data_as_of')
    seconds = math.ceil(age.total_seconds())
    # Fixture precondition: room for a bound below the age and one at/above it.
    assert 1 < seconds <= MAX_BOUND_SECONDS, seconds
    return seconds


def policy(observation, *, older=False, market='EGX'):
    """One bound at/above the record's age (first bucket) or, with older, below it (last)."""
    seconds = age_seconds(observation)
    bound = seconds - 1 if older else seconds
    detected = _time(observation[1]['detected_at'], 'detected_at')
    return DataAgePolicy(policy_version='fixture-checked-age-1', market=market,
                         registered_at=detected - timedelta(days=2),
                         upper_bounds_seconds=(bound,))


def run(observation, outcomes, attestations, as_of=NOW, rules=None):
    return aggregate_egx_checked_decision_outcomes_by_data_age(
        (observation,), outcomes, attestations, assumptions=ZERO, as_of=as_of,
        policy=policy(observation) if rules is None else rules)


def counts(**values):
    return {key: values.get(key, 0) for key in COUNT_KEYS}


def bucket(result, index):
    return result['data_age_metrics']['buckets'][index]


def only_segment(result, index=0):
    (segment,) = bucket(result, index)['metrics']['segments']
    return segment


def test_attested_clear_outcome_is_counted_in_its_age_bucket(checked):
    outcome_identity, outcome, observation, att = parts(checked)
    rules = policy(observation)
    out = run(observation, (outcome,), (att,), rules=rules)
    assert out['contract_version'] == CONTRACT_VERSION == (
        'decision-outcome-checked-data-age-metrics-v1')
    assert out['market'] == 'EGX' and out['as_of'] == att['check_as_of']
    assert out['attestation_counts'] == counts(ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF=1)
    assert (out['outcomes_supplied'], out['attestations_supplied']) == (1, 1)
    assert (out['outcomes_counted'], out['outcomes_not_counted']) == (1, 0)
    assert out['attested_after_as_of'] == []
    # Embedded bucket metrics are exactly the unchanged data-age component's
    # output over the same population and the counted outcomes.
    assert out['data_age_metrics'] == aggregate_decision_outcomes_by_data_age(
        (observation,), (outcome,), market='EGX', assumptions=ZERO, as_of=NOW, policy=rules)
    # The selection is exactly the checked-metrics component's.
    checked_report = aggregate_egx_checked_decision_outcomes(
        (observation,), (outcome,), (att,), assumptions=ZERO, as_of=NOW)
    assert out['partition'] == checked_report['partition']
    assert out['attestation_counts'] == checked_report['attestation_counts']
    segment = only_segment(out)
    assert segment['observations'] == 1 and segment['status_counts']['CLOSED'] == 1
    # Every bucket is still reported in fixed order; the other is empty.
    by_age = out['data_age_metrics']
    labels = list(bucket_labels(rules.upper_bounds_seconds))
    assert by_age['bucket_labels'] == labels
    assert by_age['bucket_counts'] == {labels[0]: 1, labels[1]: 0}
    assert by_age['cross_bucket_headline'] == 'NOT_COMPUTED'
    assert not {'segments', 'hit_rate', 'expectancy_net_r', 'metrics', 'buckets'} & set(out)
    assert {key: out[key] for key in SAFETY} == SAFETY
    assert out['partition']['entries'][0]['outcome_identity'] == outcome_identity
    json.dumps(out, allow_nan=False)


def test_bucket_is_assigned_by_retained_data_age_not_outcome_time(checked):
    _, outcome, observation, att = parts(checked)
    out = run(observation, (outcome,), (att,), rules=policy(observation, older=True))
    assert bucket(out, 0)['observations'] == 0
    assert bucket(out, 1)['observations'] == 1
    assert only_segment(out, 1)['status_counts']['CLOSED'] == 1
    assert out['outcomes_counted'] == 1
    assert out['data_age_metrics']['population_count'] == 1


def test_unattested_outcome_is_not_evaluated_inside_its_age_bucket(checked):
    _, outcome, observation, _ = parts(checked)
    out = run(observation, (outcome,), ())
    assert out['attestation_counts'] == counts(UNATTESTED=1)
    assert (out['outcomes_counted'], out['outcomes_not_counted']) == (0, 1)
    assert bucket(out, 0)['observations'] == 1
    segment = only_segment(out)
    # The unchecked (CLOSED) outcome stays in the bucket's sample as NOT_EVALUATED.
    assert segment['observations'] == 1 and segment['closed'] == 0
    assert segment['status_counts'][NOT_EVALUATED] == 1
    assert segment['missing_data_rate'] == '1'


def test_invalid_attestation_is_not_evaluated_inside_its_age_bucket(checked):
    _, outcome, observation, att = parts(checked)
    forged = deepcopy(att)
    forged['strategy_version'] = 'forged'
    out = run(observation, (outcome,), (forged,))
    assert out['attestation_counts'] == counts(ATTESTATION_INVALID=1)
    assert only_segment(out)['status_counts'][NOT_EVALUATED] == 1


def test_later_attestation_cannot_leak_into_an_earlier_age_report(checked):
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


def test_policy_and_integrity_failures_fail_closed(checked):
    _, outcome, observation, att = parts(checked)
    with pytest.raises(ValueError, match='market mismatch'):
        run(observation, (outcome,), (att,), rules=policy(observation, market='US'))
    with pytest.raises(ValueError, match='DataAgePolicy'):
        run(observation, (outcome,), (att,), rules=policy(observation).model_dump())
    late_policy = DataAgePolicy(policy_version='fixture-checked-age-late', market='EGX',
                                registered_at=NOW + timedelta(seconds=1),
                                upper_bounds_seconds=(age_seconds(observation),))
    with pytest.raises(ValueError, match='registered after as_of'):
        run(observation, (outcome,), (att,), rules=late_policy)
    # The unchanged checked selection gate runs first over everything supplied.
    with pytest.raises(ValueError, match='as_of'):
        run(observation, (outcome,), (att,), as_of=NOW - timedelta(minutes=1))
    with pytest.raises(ValueError, match='more than one outcome'):
        run(observation, (outcome, outcome), ())
    with pytest.raises(ValueError, match='timezone-aware'):
        run(observation, (outcome,), (att,), as_of=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match='OutcomeAssumptions'):
        aggregate_egx_checked_decision_outcomes_by_data_age(
            (observation,), (outcome,), (att,), assumptions=ZERO.model_dump(), as_of=NOW,
            policy=policy(observation))


def test_pure_deterministic_and_inputs_not_mutated(checked):
    _, outcome, observation, att = parts(checked)
    rules = policy(observation)
    before = deepcopy((observation, outcome, att, rules.model_dump(mode='json')))
    first = run(observation, (outcome,), (att,), rules=rules)
    second = run(observation, (outcome,), (att,), rules=rules)
    assert first == second and canonical_bytes(first) == canonical_bytes(second)
    assert (observation, outcome, att, rules.model_dump(mode='json')) == before
