"""decision-outcome-attestation-partition-v1: outcomes split by re-verified attestation."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json

import pytest

from app.decision_observation import canonical_bytes
from app.decision_outcome_action_attestation import attest_outcome_corporate_actions
from app.decision_outcome_attested import (
    CONTRACT_VERSION, STATUSES, attested_clear_outcomes, partition_egx_attested_outcomes)
from app.decision_outcome_metrics import aggregate_decision_outcomes
from tests.test_decision_outcome_action_attestation import checked, report  # noqa: F401
from tests.test_decision_outcome_producer import NOW
from tests.test_decision_outcome_session_bars import ZERO

SAFETY = {'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR', 'absence_is_proof': False,
          'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
          'retained_outcome_corporate_actions': 'NOT_CHECKED_UNCHANGED',
          'check_recomputation': 'NOT_PERFORMED',
          'review_verification': 'CALLER_ASSERTED_UNVERIFIED', 'price_adjustment': 'NOT_APPLIED',
          'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
          'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
          'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}
LATER = NOW + timedelta(days=1)


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The stores refuse group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


def parts(checked):
    outcome_identity, outcome, observation, clear = checked
    attestation = attest_outcome_corporate_actions(outcome_identity, outcome, observation, clear)
    return outcome_identity, outcome, observation, attestation


def digest(record):
    return sha256(canonical_bytes(record)).hexdigest()


def aggregate(observation, outcomes):
    return aggregate_decision_outcomes((observation,), outcomes, market='EGX', assumptions=ZERO,
                                       as_of=LATER)


def test_verified_attestation_marks_the_outcome_attested_clear(checked):
    outcome_identity, outcome, observation, att = parts(checked)
    out = partition_egx_attested_outcomes((observation,), (outcome,), (att,))
    assert out['contract_version'] == CONTRACT_VERSION == 'decision-outcome-attestation-partition-v1'
    assert out['market'] == 'EGX'
    assert (out['outcomes_supplied'], out['attestations_supplied']) == (1, 1)
    assert tuple(out['counts']) == STATUSES
    assert out['counts'] == {'ATTESTED_CLEAR': 1, 'UNATTESTED': 0, 'ATTESTATION_INVALID': 0}
    (entry,) = out['entries']
    assert entry['outcome_identity'] == outcome_identity == digest(outcome)
    assert entry['observation_identity'] == observation[0] == outcome['observation_identity']
    for key in ('decision_id', 'symbol', 'strategy_id', 'strategy_version', 'config_id'):
        assert entry[key] == outcome[key]
    assert entry['assumptions_version'] == 'fixture-zero'
    assert entry['outcome_status'] == outcome['status'] == 'CLOSED'
    assert (entry['attestation_status'], entry['reason']) == (
        'ATTESTED_CLEAR', 'VERIFIED_CLEAR_CHECK_OVER_PATH')
    assert entry['attestation_identity'] == digest(att)
    assert (entry['check_as_of'], entry['check_timing']) == (att['check_as_of'], 'AT_EVALUATION')
    assert out['attestations_without_supplied_outcome'] == []
    assert {key: out[key] for key in SAFETY} == SAFETY
    json.dumps(out, allow_nan=False)
    # The attested records feed the unchanged aggregate.
    attested = attested_clear_outcomes((observation,), (outcome,), (att,))
    assert attested == (outcome,)
    (segment,) = aggregate(observation, attested)['segments']
    assert segment['status_counts']['CLOSED'] == 1 and segment['observations'] == 1


def test_later_check_is_attested_and_labelled(checked):
    outcome_identity, outcome, observation, _ = checked
    later = attest_outcome_corporate_actions(
        outcome_identity, outcome, observation, report(observation, as_of=LATER))
    (entry,) = partition_egx_attested_outcomes((observation,), (outcome,), (later,))['entries']
    assert entry['attestation_status'] == 'ATTESTED_CLEAR'
    assert entry['check_timing'] == 'AFTER_EVALUATION'
    assert entry['check_as_of'] == '2026-11-10T15:00:00Z'


def test_missing_attestation_is_unattested_not_clear_and_never_dropped(checked):
    _, outcome, observation, _ = parts(checked)
    out = partition_egx_attested_outcomes((observation,), (outcome,), ())
    assert out['counts'] == {'ATTESTED_CLEAR': 0, 'UNATTESTED': 1, 'ATTESTATION_INVALID': 0}
    (entry,) = out['entries']
    assert (entry['attestation_status'], entry['reason']) == ('UNATTESTED', 'NO_ATTESTATION_SUPPLIED')
    assert entry['attestation_identity'] is entry['check_as_of'] is entry['check_timing'] is None
    assert out['unattested_meaning'] == 'NOT_CHECKED_NOT_CLEAR' and out['absence_is_proof'] is False
    # Restricting the aggregate to attested outcomes keeps the observation as
    # NOT_EVALUATED, so an unchecked loss cannot vanish from the sample.
    assert attested_clear_outcomes((observation,), (outcome,), ()) == ()
    (segment,) = aggregate(observation, ())['segments']
    assert segment['observations'] == 1 and segment['closed'] == 0
    assert segment['status_counts']['NOT_EVALUATED'] == 1


@pytest.mark.parametrize('key, value', [('strategy_version', 'forged'),
                                        ('outcome_status', 'NO_ENTRY'),
                                        ('assumptions_version', 'other')])
def test_attestation_that_does_not_verify_is_visible_and_not_attested(checked, key, value):
    _, outcome, observation, att = parts(checked)
    forged = deepcopy(att)
    forged[key] = value        # structurally valid, but not what this outcome yields
    out = partition_egx_attested_outcomes((observation,), (outcome,), (forged,))
    assert out['counts'] == {'ATTESTED_CLEAR': 0, 'UNATTESTED': 0, 'ATTESTATION_INVALID': 1}
    (entry,) = out['entries']
    assert (entry['attestation_status'], entry['reason']) == (
        'ATTESTATION_INVALID', 'VERIFICATION_FAILED')
    assert entry['attestation_identity'] == digest(forged)
    assert entry['check_as_of'] is entry['check_timing'] is None
    assert attested_clear_outcomes((observation,), (outcome,), (forged,)) == ()


def test_attestation_without_supplied_outcome_is_listed_not_matched(checked):
    outcome_identity, _, observation, att = parts(checked)
    out = partition_egx_attested_outcomes((observation,), (), (att,))
    assert out['outcomes_supplied'] == 0 and out['entries'] == []
    assert out['counts'] == {status: 0 for status in STATUSES}
    assert out['attestations_without_supplied_outcome'] == [
        {'attestation_identity': digest(att), 'outcome_identity': outcome_identity}]


def test_integrity_failures_fail_closed(checked):
    _, outcome, observation, att = parts(checked)
    # An outcome without its observation is refused, never silently dropped.
    with pytest.raises(ValueError, match='population observation'):
        partition_egx_attested_outcomes((), (outcome,), ())
    with pytest.raises(ValueError, match='duplicate outcome'):
        partition_egx_attested_outcomes((observation,), (outcome, outcome), ())
    with pytest.raises(ValueError, match='duplicate observation'):
        partition_egx_attested_outcomes((observation, observation), (outcome,), ())
    later = attest_outcome_corporate_actions(
        checked[0], outcome, observation, report(observation, as_of=LATER))
    with pytest.raises(ValueError, match='more than once'):
        partition_egx_attested_outcomes((observation,), (outcome,), (att, later))
    edited = deepcopy(outcome)
    edited['symbol'] = 'EDIT'
    with pytest.raises(ValueError, match='does not match its observation'):
        partition_egx_attested_outcomes((observation,), (edited,), ())
    broken = deepcopy(att)
    del broken['check']
    with pytest.raises(ValueError, match='field set'):
        partition_egx_attested_outcomes((observation,), (outcome,), (broken,))
    record = deepcopy(observation[1])
    record['symbol'] = 'EDIT'
    with pytest.raises(ValueError):
        partition_egx_attested_outcomes(((observation[0], record),), (outcome,), ())
    for population, outcomes, attestations in (([observation], (outcome,), ()),
                                               ((observation,), [outcome], ()),
                                               ((observation,), (outcome,), [att]),
                                               ((observation,), ('x',), ()),
                                               ((observation,), (outcome,), ('x',))):
        with pytest.raises(ValueError):
            partition_egx_attested_outcomes(population, outcomes, attestations)
        with pytest.raises(ValueError):
            attested_clear_outcomes(population, outcomes, attestations)


def test_pure_deterministic_and_inputs_not_mutated(checked):
    _, outcome, observation, att = parts(checked)
    before = deepcopy((observation, outcome, att))
    first = partition_egx_attested_outcomes((observation,), (outcome,), (att,))
    second = partition_egx_attested_outcomes((observation,), (outcome,), (att,))
    assert first == second and canonical_bytes(first) == canonical_bytes(second)
    attested = attested_clear_outcomes((observation,), (outcome,), (att,))
    assert (observation, outcome, att) == before
    # Returned records are fresh copies, not the caller's objects.
    assert attested[0] is not outcome
    attested[0]['symbol'] = 'MUTATED'
    assert outcome['symbol'] != 'MUTATED'
