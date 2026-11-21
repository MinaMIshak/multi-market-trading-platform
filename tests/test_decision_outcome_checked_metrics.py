"""decision-outcome-checked-metrics-v1: metrics over attested-CLEAR outcomes only."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json

import pytest

from app.decision_observation import canonical_bytes
from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_action_attestation import attest_outcome_corporate_actions
from app.decision_outcome_attested import partition_egx_attested_outcomes
from app.decision_outcome_checked_metrics import (
    ATTESTATION_TIME_RULE, CONTRACT_VERSION, COUNT_KEYS, aggregate_egx_checked_decision_outcomes)
from app.decision_outcome_metrics import aggregate_decision_outcomes
from tests.test_decision_outcome_action_attestation import checked, report  # noqa: F401
from tests.test_decision_outcome_producer import NOW
from tests.test_decision_outcome_session_bars import ZERO

LATER = NOW + timedelta(days=1)
SAFETY = {'outcome_basis': 'ATTESTED_CLEAR_ONLY',
          'not_counted_treatment': 'NOT_EVALUATED_IN_SAME_POPULATION_NEVER_DROPPED',
          'unrestricted_metrics': 'NOT_REPORTED',
          'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR', 'absence_is_proof': False,
          'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
          'attestation_completeness': 'CALLER_ASSERTED_UNVERIFIED',
          'attestation_retention_time': 'NOT_RECORDED_NOT_CHECKED',
          'check_recomputation': 'NOT_PERFORMED',
          'review_verification': 'CALLER_ASSERTED_UNVERIFIED', 'price_adjustment': 'NOT_APPLIED',
          'sample_assessment': 'NOT_ASSESSED', 'cross_segment_headline': 'NOT_COMPUTED',
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
    return aggregate_egx_checked_decision_outcomes(
        (observation,), outcomes, attestations, assumptions=ZERO, as_of=as_of)


def counts(**values):
    return {key: values.get(key, 0) for key in COUNT_KEYS}


def test_attested_clear_outcome_is_counted_in_unchanged_metrics(checked):
    outcome_identity, outcome, observation, att = parts(checked)
    out = run(observation, (outcome,), (att,))
    assert out['contract_version'] == CONTRACT_VERSION == 'decision-outcome-checked-metrics-v1'
    assert out['attestation_time_rule'] == ATTESTATION_TIME_RULE
    assert out['market'] == 'EGX'
    # AT_EVALUATION: the check, the evaluation and as_of are the same instant.
    assert out['as_of'] == out['metrics']['as_of'] == att['check_as_of']
    assert tuple(out['attestation_counts']) == COUNT_KEYS
    assert out['attestation_counts'] == counts(ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF=1)
    assert (out['outcomes_supplied'], out['attestations_supplied']) == (1, 1)
    assert (out['outcomes_counted'], out['outcomes_not_counted']) == (1, 0)
    assert out['attested_after_as_of'] == []
    # The embedded partition and metrics are exactly the unchanged components' output.
    assert out['partition'] == partition_egx_attested_outcomes((observation,), (outcome,), (att,))
    assert out['metrics'] == aggregate_decision_outcomes(
        (observation,), (outcome,), market='EGX', assumptions=ZERO, as_of=NOW)
    (segment,) = out['metrics']['segments']
    assert segment['observations'] == 1 and segment['status_counts']['CLOSED'] == 1
    assert {key: out[key] for key in SAFETY} == SAFETY
    assert out['partition']['entries'][0]['outcome_identity'] == outcome_identity
    json.dumps(out, allow_nan=False)


def test_unattested_outcome_is_not_evaluated_never_dropped(checked):
    _, outcome, observation, _ = parts(checked)
    out = run(observation, (outcome,), ())
    assert out['attestation_counts'] == counts(UNATTESTED=1)
    assert (out['outcomes_counted'], out['outcomes_not_counted']) == (0, 1)
    assert out['metrics']['population_count'] == 1
    assert (out['metrics']['outcome_count'], out['metrics']['not_evaluated_count']) == (0, 1)
    (segment,) = out['metrics']['segments']
    # The unchecked outcome (a CLOSED result) stays in the sample as NOT_EVALUATED.
    assert segment['observations'] == 1 and segment['closed'] == 0
    assert segment['status_counts']['NOT_EVALUATED'] == 1
    assert segment['missing_data_rate'] == '1'


def test_invalid_attestation_is_visible_and_not_counted(checked):
    _, outcome, observation, att = parts(checked)
    forged = deepcopy(att)
    forged['strategy_version'] = 'forged'
    out = run(observation, (outcome,), (forged,))
    assert out['attestation_counts'] == counts(ATTESTATION_INVALID=1)
    assert out['outcomes_counted'] == 0
    assert out['metrics']['segments'][0]['status_counts']['NOT_EVALUATED'] == 1


def test_attestation_checked_after_as_of_cannot_leak_into_earlier_report(checked):
    outcome_identity, outcome, observation, _ = checked
    later = attest_outcome_corporate_actions(
        outcome_identity, outcome, observation, report(observation, as_of=LATER))
    early = run(observation, (outcome,), (later,), as_of=NOW)
    assert early['attestation_counts'] == counts(ATTESTED_CLEAR_AFTER_AS_OF=1)
    assert early['attested_after_as_of'] == [outcome_identity]
    assert early['outcomes_counted'] == 0
    assert early['metrics']['segments'][0]['status_counts']['NOT_EVALUATED'] == 1
    # The partition still shows the verified attestation; only the time rule excludes it.
    assert early['partition']['counts']['ATTESTED_CLEAR'] == 1
    # At the check's own time the same evidence is counted.
    current = run(observation, (outcome,), (later,), as_of=LATER)
    assert current['attestation_counts'] == counts(ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF=1)
    assert current['attested_after_as_of'] == [] and current['outcomes_counted'] == 1
    assert current['metrics']['segments'][0]['status_counts']['CLOSED'] == 1


def test_integrity_failures_fail_closed(checked):
    _, outcome, observation, att = parts(checked)
    # An outcome evaluated after as_of is refused by the unchanged aggregate gate.
    with pytest.raises(ValueError, match='as_of'):
        run(observation, (outcome,), (att,), as_of=NOW - timedelta(minutes=1))
    # Unattested outcomes are still integrity-checked (assumptions must match).
    other = OutcomeAssumptions.model_validate(
        {**ZERO.model_dump(mode='python'), 'assumptions_version': 'other'})
    with pytest.raises(ValueError, match='assumptions'):
        aggregate_egx_checked_decision_outcomes(
            (observation,), (outcome,), (), assumptions=other, as_of=NOW)
    with pytest.raises(ValueError, match='population observation'):
        aggregate_egx_checked_decision_outcomes((), (outcome,), (), assumptions=ZERO, as_of=NOW)
    with pytest.raises(ValueError, match='more than one outcome'):
        run(observation, (outcome, outcome), ())
    edited = deepcopy(outcome)
    edited['symbol'] = 'EDIT'
    with pytest.raises(ValueError):
        run(observation, (edited,), ())
    with pytest.raises(ValueError, match='timezone-aware'):
        run(observation, (outcome,), (att,), as_of=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        run(observation, (outcome,), [att])
    with pytest.raises(ValueError):
        run(observation, (outcome,), ('x',))
    with pytest.raises(ValueError, match='OutcomeAssumptions'):
        aggregate_egx_checked_decision_outcomes(
            (observation,), (outcome,), (att,), assumptions=ZERO.model_dump(), as_of=NOW)


def test_pure_deterministic_and_inputs_not_mutated(checked):
    _, outcome, observation, att = parts(checked)
    before = deepcopy((observation, outcome, att))
    first = run(observation, (outcome,), (att,))
    second = run(observation, (outcome,), (att,))
    assert first == second and canonical_bytes(first) == canonical_bytes(second)
    assert (observation, outcome, att) == before
    assert first['metrics']['outcomes_sha256'] == sha256(canonical_bytes([outcome])).hexdigest()
