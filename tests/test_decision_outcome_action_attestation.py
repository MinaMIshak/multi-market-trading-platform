"""decision-outcome-action-attestation-v1: retained outcome bound to its CLEAR action check."""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
from uuid import UUID

import pytest

from app.decision_observation import canonical_bytes
from app.decision_outcome_action_attestation import (
    CHECK_FIELDS, CONTRACT_VERSION, PATH_RULE, attest_outcome_corporate_actions,
    verify_outcome_action_attestation)
from app.decision_outcome_corporate_actions import WINDOW_RULE, check_egx_outcome_corporate_actions
from app.decision_outcome_producer import ACTION_PATH_RULE
from app.decision_outcome_store import load_retained_decision_outcomes
from tests.test_decision_outcome_corporate_actions import (
    REVIEWS, SOURCE, action, evidence as action_evidence)
from tests.test_decision_outcome_producer import (
    NOW, OTHER, decision, observe, produce, selection)
from tests.test_decision_outcome_session_bars import ZERO

SAFETY = {'price_adjustment': 'NOT_APPLIED', 'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
          'execution_evidence': 'NONE', 'performance_claim': 'NONE', 'mode': 'SHADOW',
          'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}
PATH = date(2026, 11, 9)          # Cairo date of the fixture path completion (14:00Z)


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The stores refuse group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


@pytest.fixture
def checked(tmp_path):
    """A real CLEAR-gated producer run: (outcome identity, outcome, observation, check)."""
    observations, outcomes = tmp_path / 'observations', tmp_path / 'outcomes'
    observations.mkdir(mode=0o700)
    outcomes.mkdir(mode=0o700)
    (observation,) = observe(observations)
    # Actions only outside the window (before the data_as_of date, after the path).
    doc = action_evidence((action(3), action(12)))
    out = produce((observations, outcomes), actions={observation[0]: selection(doc)})
    (entry,) = out['outcomes']
    assert (entry['status'], entry['corporate_action_status']) == ('RETAINED', 'CLEAR')
    (outcome,) = load_retained_decision_outcomes(str(outcomes), market='EGX', assumptions=ZERO)
    return entry['outcome_identity'], outcome, observation, entry['corporate_action_check']


def report(observation, doc=None, path=PATH, as_of=NOW):
    identity, record = observation
    return check_egx_outcome_corporate_actions(
        identity, record, action_evidence() if doc is None else doc,
        source_ingestion_id=SOURCE, review_ingestion_ids=REVIEWS, path_through=path, as_of=as_of)


def test_clear_check_attests_the_retained_outcome(checked):
    outcome_identity, outcome, observation, check = checked
    identity, record = observation
    att = attest_outcome_corporate_actions(outcome_identity, outcome, observation, check)
    assert att['contract_version'] == CONTRACT_VERSION == 'decision-outcome-action-attestation-v1'
    assert att['outcome_identity'] == outcome_identity == sha256(canonical_bytes(outcome)).hexdigest()
    assert att['observation_identity'] == identity == outcome['observation_identity']
    for key in ('decision_id', 'symbol', 'instrument_id', 'strategy_id', 'strategy_version',
                'config_id'):
        assert att[key] == record[key] == outcome[key]
    assert att['market'] == 'EGX'
    assert att['assumptions_version'] == 'fixture-zero'
    assert att['outcome_status'] == outcome['status'] == 'CLOSED'
    assert att['outcome_evaluated_at'] == outcome['evaluated_at'] == '2026-11-09T15:00:00Z'
    assert att['path_complete_through_at'] == '2026-11-09T14:00:00Z'
    assert att['path_rule'] == PATH_RULE == ACTION_PATH_RULE
    assert att['window_rule'] == WINDOW_RULE
    assert att['corporate_action_status'] == 'CLEAR'
    # The producer checks at its own clock, which is also the evaluation time.
    assert att['check_as_of'] == '2026-11-09T15:00:00Z'
    assert att['check_timing'] == 'AT_EVALUATION'
    assert att['check'] == check == report(observation, action_evidence((action(3), action(12))))
    assert att['check_sha256'] == sha256(canonical_bytes(check)).hexdigest()
    assert att['check_recomputation'] == 'NOT_PERFORMED'
    assert att['review_verification'] == 'CALLER_ASSERTED_UNVERIFIED'
    assert {key: att[key] for key in SAFETY} == SAFETY
    assert set(check) == CHECK_FIELDS
    json.dumps(att, allow_nan=False)
    # Deterministic and verifiable.
    again = attest_outcome_corporate_actions(outcome_identity, deepcopy(outcome),
                                             deepcopy(observation), deepcopy(check))
    assert canonical_bytes(again) == canonical_bytes(att)
    assert verify_outcome_action_attestation(att, outcome_identity, outcome, observation) == att


def test_inputs_are_not_mutated(checked):
    before = deepcopy(checked)
    outcome_identity, outcome, observation, check = checked
    attest_outcome_corporate_actions(outcome_identity, outcome, observation, check)
    assert checked == before


def test_later_check_is_labelled_and_earlier_check_refused(checked):
    outcome_identity, outcome, observation, _ = checked
    later = report(observation, as_of=NOW + timedelta(days=1))
    att = attest_outcome_corporate_actions(outcome_identity, outcome, observation, later)
    assert att['check_timing'] == 'AFTER_EVALUATION'
    assert att['check_as_of'] == '2026-11-10T15:00:00Z'
    # A check made before the outcome was evaluated is not attached to it.
    early = report(observation, as_of=NOW - timedelta(minutes=1))
    with pytest.raises(ValueError, match='before outcome evaluation'):
        attest_outcome_corporate_actions(outcome_identity, outcome, observation, early)


def test_actions_in_path_cannot_be_attested(checked):
    outcome_identity, outcome, observation, _ = checked
    # A dividend effective on Sunday 2026-11-08, inside the path.
    in_path = report(observation, action_evidence((action(8),)))
    assert in_path['status'] == 'ACTIONS_IN_PATH'
    with pytest.raises(ValueError, match='status'):
        attest_outcome_corporate_actions(outcome_identity, outcome, observation, in_path)


def test_check_must_cover_exactly_this_outcome_path(checked):
    outcome_identity, outcome, observation, _ = checked
    # A CLEAR check through an earlier date leaves the last path session unchecked.
    short = report(observation, path=date(2026, 11, 8))
    assert short['status'] == 'CLEAR'
    with pytest.raises(ValueError, match='path completion date'):
        attest_outcome_corporate_actions(outcome_identity, outcome, observation, short)
    # A longer window is a different check than the producer's rule.
    long = report(observation, path=date(2026, 11, 10), as_of=NOW + timedelta(days=1))
    with pytest.raises(ValueError, match='path completion date'):
        attest_outcome_corporate_actions(outcome_identity, outcome, observation, long)


def test_check_for_another_observation_is_refused(checked, tmp_path):
    outcome_identity, outcome, observation, _ = checked
    other_dir = tmp_path / 'other'
    other_dir.mkdir(mode=0o700)
    (other,) = observe(other_dir, decision(symbol='OTHR', instrument=OTHER))
    foreign = report(other, action_evidence(instrument=OTHER))
    assert foreign['status'] == 'CLEAR'
    with pytest.raises(ValueError, match='does not match the observation'):
        attest_outcome_corporate_actions(outcome_identity, outcome, observation, foreign)
    # The outcome is not bound to the other observation either.
    with pytest.raises(ValueError):
        attest_outcome_corporate_actions(outcome_identity, outcome, other, foreign)


def test_outcome_must_be_the_retained_bytes_of_this_observation(checked):
    outcome_identity, outcome, observation, check = checked
    with pytest.raises(ValueError, match='hash mismatch'):
        attest_outcome_corporate_actions('0' * 64, outcome, observation, check)
    with pytest.raises(ValueError):
        attest_outcome_corporate_actions(outcome_identity.upper(), outcome, observation, check)
    edited = deepcopy(outcome)
    edited['symbol'] = 'EDIT'
    with pytest.raises(ValueError, match='does not match its observation'):
        attest_outcome_corporate_actions(sha256(canonical_bytes(edited)).hexdigest(), edited,
                                         observation, check)
    with pytest.raises(ValueError, match='hash mismatch'):
        attest_outcome_corporate_actions(outcome_identity, edited, observation, check)
    identity, record = observation
    tampered = deepcopy(record)
    tampered['symbol'] = 'EDIT'
    for bad in ((identity, tampered), [identity, record], (identity,), None):
        with pytest.raises(ValueError):
            attest_outcome_corporate_actions(outcome_identity, outcome, bad, check)
    for bad in (None, [outcome], {}):
        with pytest.raises(ValueError):
            attest_outcome_corporate_actions(outcome_identity, bad, observation, check)


def _set(path, value):
    def mutate(check):
        target = check
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


def _drop(key):
    return lambda check: check.pop(key)


HEX = str(UUID(int=0xABC))
LOW, HIGH = str(UUID(int=202)), str(UUID(int=303))


@pytest.mark.parametrize('mutate', [
    _set(('status',), 'ACTIONS_IN_PATH'),
    _set(('clear_basis',), None),
    _set(('actions_in_path',), [{'event_id': 'ev-8'}]),
    _set(('contract_version',), 'decision-outcome-corporate-actions-v0'),
    _set(('market',), 'US'),
    _set(('price_adjustment',), 'APPLIED'),
    _set(('outcome',), 'CLOSED'),
    _set(('performance_claim',), 'EDGE'),
    _set(('mode',), 'LIVE'),
    _set(('live_money',), True),
    _set(('live_money',), 0),
    _set(('execution_allowed',), True),
    _set(('promotion',), 'AUTHORIZED'),
    _set(('extra',), 1),
    _drop('as_of'),
    _set(('observation_identity',), 'f' * 64),
    _set(('decision_id',), 'f' * 64),
    _set(('symbol',), 'OTHR'),
    _set(('instrument_id',), str(OTHER)),
    _set(('window', 'rule'), 'OTHER_WINDOW'),
    _set(('window', 'timezone'), 'UTC'),
    _set(('window', 'first_date'), '2026-11-06'),
    _set(('window', 'first_date'), '2026-11-5'),
    _set(('window', 'last_date'), '2026-11-08'),
    _set(('window', 'extra'), 1),
    _set(('window',), None),
    _set(('as_of',), '2026-11-09T15:00:00'),
    _set(('as_of',), 'later'),
    _set(('evidence', 'contract'), 'us-actions-v1'),
    _set(('evidence', 'complete'), False),
    _set(('evidence', 'complete'), 1),
    _set(('evidence', 'review_verification'), 'VERIFIED'),
    _set(('evidence', 'coverage_start'), '2026-11-06'),
    _set(('evidence', 'coverage_end'), '2026-11-08'),
    _set(('evidence', 'published_at'), '2026-11-09T15:00:01Z'),
    _set(('evidence', 'actions_declared'), -1),
    _set(('evidence', 'actions_declared'), True),
    _set(('evidence', 'source_ingestion_id'), HEX.upper()),
    _set(('evidence', 'review_ingestion_ids'), []),
    _set(('evidence', 'review_ingestion_ids'), [HIGH, LOW]),
    _set(('evidence', 'review_ingestion_ids'), [LOW, LOW]),
    _set(('evidence', 'review_ingestion_ids'), [SOURCE]),
    _set(('evidence', 'review_ingestion_ids'), [HEX.upper()]),
    _set(('evidence', 'extra'), 1),
])
def test_tampered_check_fails_closed(checked, mutate):
    outcome_identity, outcome, observation, check = checked
    tampered = deepcopy(check)
    mutate(tampered)
    with pytest.raises(ValueError):
        attest_outcome_corporate_actions(outcome_identity, outcome, observation, tampered)
    # The untampered report still attests (the mutation alone caused the refusal).
    attest_outcome_corporate_actions(outcome_identity, outcome, observation, check)


def test_non_json_check_is_refused(checked):
    outcome_identity, outcome, observation, check = checked
    for bad in (None, [check], {**check, 'as_of': NOW}):
        with pytest.raises(ValueError):
            attest_outcome_corporate_actions(outcome_identity, outcome, observation, bad)


def test_verify_refuses_edited_or_repointed_attestations(checked, tmp_path):
    outcome_identity, outcome, observation, check = checked
    att = attest_outcome_corporate_actions(outcome_identity, outcome, observation, check)
    for key, value in (('live_money', 0), ('check_timing', 'AFTER_EVALUATION'),
                       ('outcome_status', 'NO_ENTRY'), ('check_sha256', '0' * 64),
                       ('extra', 1)):
        edited = {**att, key: value}
        with pytest.raises(ValueError, match='does not match'):
            verify_outcome_action_attestation(edited, outcome_identity, outcome, observation)
    dropped = {key: value for key, value in att.items() if key != 'check_recomputation'}
    with pytest.raises(ValueError, match='does not match'):
        verify_outcome_action_attestation(dropped, outcome_identity, outcome, observation)
    # Without its embedded check, or against another outcome, nothing verifies.
    with pytest.raises(ValueError):
        verify_outcome_action_attestation({**att, 'check': None}, outcome_identity, outcome,
                                          observation)
    edited_outcome = deepcopy(outcome)
    edited_outcome['evaluated_at'] = '2026-11-09T15:00:01Z'
    with pytest.raises(ValueError):
        verify_outcome_action_attestation(
            att, sha256(canonical_bytes(edited_outcome)).hexdigest(), edited_outcome, observation)
    for bad in (None, [att], json.dumps(att)):
        with pytest.raises(ValueError):
            verify_outcome_action_attestation(bad, outcome_identity, outcome, observation)
