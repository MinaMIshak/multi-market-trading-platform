"""Write-once sidecar retention of decision-outcome-action-attestation-v1 records."""
from copy import deepcopy
from datetime import timedelta
import fcntl
from hashlib import sha256
import json
import os

import pytest

from app.decision_observation import canonical_bytes
from app.decision_outcome_action_attestation import attest_outcome_corporate_actions
from app.decision_outcome_attestation_store import (
    ATTESTATION_FIELDS, CHECK_TIME_FIELDS, RESULT_SCHEMA, STORE_REASONS,
    OutcomeAttestationStoreError, RetainedOutcomeAttestationError,
    load_retained_outcome_attestations, read_retained_outcome_attestation,
    retain_outcome_action_attestation)
from tests.test_decision_outcome_action_attestation import checked, report  # noqa: F401
from tests.test_decision_outcome_corporate_actions import action, evidence as action_evidence
from tests.test_decision_outcome_producer import NOW

SAFETY = {'price_adjustment': 'NOT_APPLIED', 'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
          'execution_evidence': 'NONE', 'performance_claim': 'NONE', 'mode': 'SHADOW',
          'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}
LATER = NOW + timedelta(days=1)


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The stores refuse group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


@pytest.fixture
def store(tmp_path):
    directory = tmp_path / 'attestations'
    directory.mkdir(mode=0o700)
    return directory


def attest(checked, check=None):
    outcome_identity, outcome, observation, clear = checked
    return attest_outcome_corporate_actions(
        outcome_identity, outcome, observation, clear if check is None else check)


def retain(directory, attestation, checked, now=NOW):
    outcome_identity, outcome, observation, _ = checked
    return retain_outcome_action_attestation(
        str(directory), attestation, (outcome_identity, outcome), observation, now=now)


def load(directory):
    return load_retained_outcome_attestations(str(directory))


def snapshot(directory):
    return sorted((p.name, None if p.is_dir() else p.read_bytes()) for p in directory.iterdir())


def refused(reason, call, *args, **kwargs):
    assert reason in STORE_REASONS
    with pytest.raises(OutcomeAttestationStoreError) as info:
        call(*args, **kwargs)
    assert info.value.reason == reason
    return info.value


def write_record(directory, record):
    payload = canonical_bytes(record)
    digest = sha256(payload).hexdigest()
    (directory / f'{digest}.json').write_bytes(payload)
    return digest


def test_verified_attestation_is_content_addressed_and_loads_exactly(store, checked):
    att = attest(checked)
    assert set(att) == ATTESTATION_FIELDS
    out = retain(store, att, checked)
    payload = canonical_bytes(att)
    digest = sha256(payload).hexdigest()
    assert out['schema_version'] == RESULT_SCHEMA == 'decision-outcome-attestation-retention-v1'
    assert out['status'] == 'RETAINED' and out['identity'] == digest
    assert out['outcome_identity'] == checked[0] == att['outcome_identity']
    assert out['observation_identity'] == checked[2][0]
    assert (out['corporate_action_status'], out['check_timing']) == ('CLEAR', 'AT_EVALUATION')
    assert out['retained_count'] == 1 and out['store_clock'] == 'CALLER_SUPPLIED_UNVERIFIED'
    assert {key: out[key] for key in SAFETY} == SAFETY
    assert snapshot(store) == [(f'{digest}.json', payload)]
    assert os.stat(store / f'{digest}.json').st_mode & 0o222 == 0
    assert load(store) == (json.loads(payload),)
    assert read_retained_outcome_attestation(payload, expected_sha256=digest) == att


def test_empty_store_means_unattested_not_clear(store):
    assert load(store) == ()


def test_retry_differing_only_in_check_time_keeps_the_first(store, checked):
    assert CHECK_TIME_FIELDS == ('check_as_of', 'check_timing', 'check_sha256')
    first = retain(store, attest(checked), checked)
    before = snapshot(store)
    assert retain(store, attest(checked), checked)['status'] == 'ALREADY_RETAINED'
    # The same CLEAR check made a day later is not a different attestation.
    later = attest(checked, report(checked[2], action_evidence((action(3), action(12))),
                                   as_of=LATER))
    assert later['check_timing'] == 'AFTER_EVALUATION'
    again = retain(store, later, checked, now=LATER)
    assert again['status'] == 'ALREADY_RETAINED' and again['identity'] == first['identity']
    assert (again['check_as_of'], again['check_timing']) == ('2026-11-09T15:00:00Z',
                                                             'AT_EVALUATION')
    assert snapshot(store) == before


def test_a_retained_attestation_cannot_be_replaced_by_other_evidence(store, checked):
    retain(store, attest(checked), checked)
    before = snapshot(store)
    # Another CLEAR check of the same path over a different evidence document.
    other = report(checked[2], action_evidence((action(3),)))
    assert other['status'] == 'CLEAR' and other != checked[3]
    refused('ATTESTATION_CONFLICT', retain, store, attest(checked, other), checked)
    assert snapshot(store) == before


def test_check_after_the_store_clock_is_rejected(store, checked):
    later = attest(checked, report(checked[2], as_of=LATER))
    refused('RECORD_REJECTED', retain, store, later, checked)
    with pytest.raises(TypeError):
        retain(store, attest(checked), checked, now=NOW.replace(tzinfo=None))
    assert snapshot(store) == []


@pytest.mark.parametrize('mutate', [
    lambda att, out, obs: att.update(live_money=0),
    lambda att, out, obs: att.update(check_timing='AFTER_EVALUATION'),
    lambda att, out, obs: att.update(outcome_status='NO_ENTRY'),
    lambda att, out, obs: att.update(extra=True),
    lambda att, out, obs: att.pop('check_recomputation'),
    lambda att, out, obs: att['check'].update(as_of='2026-11-09T14:00:00Z'),
    lambda att, out, obs: out.update(evaluated_at='2026-11-09T15:00:01Z'),
    lambda att, out, obs: obs[1].update(symbol='EDIT'),
])
def test_unverified_attestations_are_rejected_before_any_write(store, checked, mutate):
    outcome_identity, outcome, (identity, record), check = checked
    att = deepcopy(attest(checked))
    outcome, observation = deepcopy(outcome), (identity, deepcopy(record))
    mutate(att, outcome, observation)
    refused('RECORD_REJECTED', retain_outcome_action_attestation, str(store), att,
            (outcome_identity, outcome), observation, now=NOW)
    assert snapshot(store) == []


def test_malformed_arguments_are_rejected(store, checked):
    outcome_identity, outcome, observation, _ = checked
    att = attest(checked)
    for pair in (None, [outcome_identity, outcome], (outcome_identity,), ('0' * 64, outcome)):
        refused('RECORD_REJECTED', retain_outcome_action_attestation, str(store), att, pair,
                observation, now=NOW)
    for bad in (None, json.dumps(att), [att]):
        refused('RECORD_REJECTED', retain_outcome_action_attestation, str(store), bad,
                (outcome_identity, outcome), observation, now=NOW)
    assert snapshot(store) == []


def test_reader_fails_closed_on_damaged_or_forged_bytes(checked):
    att = attest(checked)
    payload = canonical_bytes(att)
    digest = sha256(payload).hexdigest()
    with pytest.raises(RetainedOutcomeAttestationError):
        read_retained_outcome_attestation(payload, expected_sha256='0' * 64)
    with pytest.raises(RetainedOutcomeAttestationError):
        read_retained_outcome_attestation(payload, expected_sha256=digest.upper())
    spaced = json.dumps(att, indent=1).encode()
    with pytest.raises(RetainedOutcomeAttestationError):
        read_retained_outcome_attestation(spaced, expected_sha256=sha256(spaced).hexdigest())
    forged_check = dict(att['check'], as_of='2026-11-10T15:00:00Z')
    for forged in ({'live_money': True}, {'promotion': 'AUTHORIZED'},
                   {'corporate_action_status': 'ACTIONS_IN_PATH'},
                   {'outcome_status': 'PENDING_PATH'}, {'check_timing': 'AFTER_EVALUATION'},
                   {'check_sha256': '0' * 64}, {'check_as_of': '2026-11-09T14:00:00Z'},
                   {'check': forged_check}, {'decision_id': 'f' * 64}, {'symbol': ''},
                   {'outcome_identity': 'X' * 64}, {'extra': 1}):
        bad = canonical_bytes({**att, **forged})
        with pytest.raises(RetainedOutcomeAttestationError):
            read_retained_outcome_attestation(bad, expected_sha256=sha256(bad).hexdigest())
    assert read_retained_outcome_attestation(payload, expected_sha256=digest) == att


def test_damaged_or_unexpected_store_content_fails_closed(store, checked):
    retain(store, attest(checked), checked)
    (store / 'notes.txt').write_text('x')
    refused('STORE_INVALID', load, store)
    refused('STORE_INVALID', retain, store, attest(checked), checked)
    (store / 'notes.txt').unlink()
    # A second attestation planted for the same outcome.
    later = attest(checked, report(checked[2], action_evidence((action(3), action(12))),
                                   as_of=LATER))
    write_record(store, later)
    refused('STORE_INVALID', load, store)


def test_outcome_records_do_not_belong_in_the_sidecar(store, checked):
    # The sidecar is a separate directory: an outcome record there fails closed.
    write_record(store, checked[1])
    refused('STORE_INVALID', load, store)
    refused('STORE_INVALID', retain, store, attest(checked), checked)


def test_directory_configuration_and_lock(tmp_path, store, checked):
    att = attest(checked)
    outcome_identity, outcome, observation, _ = checked
    refused('CONFIG_INVALID', retain_outcome_action_attestation, 'relative/dir', att,
            (outcome_identity, outcome), observation, now=NOW)
    refused('CONFIG_INVALID', load_retained_outcome_attestations, 'relative/dir')
    refused('DIRECTORY_UNAVAILABLE', retain, tmp_path / 'missing', att, checked)
    shared = tmp_path / 'shared'
    shared.mkdir()
    shared.chmod(0o770)
    refused('DIRECTORY_UNSAFE', retain, shared, att, checked)
    fd = os.open(str(store), os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        refused('STORE_BUSY', retain, store, att, checked)
    finally:
        os.close(fd)
    assert snapshot(store) == []
    assert retain(store, att, checked)['status'] == 'RETAINED'
