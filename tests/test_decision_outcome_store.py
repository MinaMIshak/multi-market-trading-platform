"""Write-once retention of terminal decision-outcome-v1 results; not fills or performance."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal as D
import fcntl
from hashlib import sha256
import json
import os

import pytest

from app.decision_observation import canonical_bytes
from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_metrics import aggregate_decision_outcomes
from app.decision_outcome_store import (
    EVALUATION_FIELDS, RESULT_SCHEMA, STORE_REASONS, TERMINAL_STATUSES, DecisionOutcomeStoreError,
    RetainedDecisionOutcomeError, load_retained_decision_outcomes, read_retained_decision_outcome,
    retain_decision_outcome)
from tests.test_decision_outcome import ABOVE, COMPLETE, FILL, ZERO, bar, observation, run

NOW = COMPLETE + timedelta(hours=2)
TARGET = bar(1, '10.5', '12.3', '9.9', '12.1')       # closes the FILL position at target
COSTS = OutcomeAssumptions(assumptions_version='fixture-costs', market='EGX',
                           entry_slippage_bps=D('10'), exit_slippage_bps=D('10'),
                           cost_bps_per_side=D('5'))
US_ZERO = OutcomeAssumptions(assumptions_version='fixture-zero', market='US',
                             entry_slippage_bps=D('0'), exit_slippage_bps=D('0'),
                             cost_bps_per_side=D('0'))
SAFETY = {'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
          'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
          'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The store refuses group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


def retain(directory, obs, outcome, now=NOW):
    return retain_decision_outcome(str(directory), obs, outcome, now=now)


def load(directory, market='EGX', assumptions=ZERO):
    return load_retained_decision_outcomes(str(directory), market=market, assumptions=assumptions)


def snapshot(directory):
    return sorted((p.name, None if p.is_dir() else p.read_bytes()) for p in directory.iterdir())


def refused(reason, call, *args, **kwargs):
    assert reason in STORE_REASONS
    with pytest.raises(DecisionOutcomeStoreError) as info:
        call(*args, **kwargs)
    assert info.value.reason == reason
    return info.value


def write_record(directory, record):
    payload = canonical_bytes(record)
    digest = sha256(payload).hexdigest()
    (directory / f'{digest}.json').write_bytes(payload)
    return digest


def test_terminal_outcome_is_content_addressed_and_loads_exactly(tmp_path):
    obs = observation()
    outcome = run([FILL, TARGET], obs=obs)
    assert outcome['status'] == 'CLOSED'
    out = retain(tmp_path, obs, outcome)
    payload = canonical_bytes(outcome)
    digest = sha256(payload).hexdigest()
    assert out['schema_version'] == RESULT_SCHEMA == 'decision-outcome-retention-v1'
    assert out['status'] == 'RETAINED' and out['identity'] == digest
    assert out['observation_identity'] == obs[0] and out['decision_id'] == obs[1]['decision_id']
    assert (out['outcome_status'], out['outcome_reason']) == ('CLOSED', None)
    assert out['assumptions_version'] == 'fixture-zero' and out['retained_count'] == 1
    assert {k: out[k] for k in SAFETY} == SAFETY
    assert snapshot(tmp_path) == [(f'{digest}.json', payload)]
    assert os.stat(tmp_path / f'{digest}.json').st_mode & 0o222 == 0
    assert load(tmp_path) == (json.loads(payload),)
    assert read_retained_decision_outcome(payload, expected_sha256=digest) == outcome


def test_loaded_outcomes_feed_the_unchanged_aggregate(tmp_path):
    obs = observation()
    retain(tmp_path, obs, run([FILL, TARGET], obs=obs))
    metrics = aggregate_decision_outcomes((obs,), load(tmp_path), market='EGX',
                                          assumptions=ZERO, as_of=NOW)
    assert metrics['contract_version'] == 'decision-outcome-metrics-v1'
    assert metrics['live_money'] is False


def test_no_entry_and_unevaluable_outcomes_are_terminal(tmp_path):
    assert TERMINAL_STATUSES == ('CLOSED', 'NO_ENTRY', 'UNEVALUABLE')
    no_entry = run(ABOVE)
    assert (no_entry['status'], no_entry['reason']) == ('NO_ENTRY', 'NOT_TOUCHED_BEFORE_EXPIRY')
    assert retain(tmp_path, observation(), no_entry)['outcome_status'] == 'NO_ENTRY'
    other = tmp_path / 'other'
    other.mkdir(mode=0o700)
    missing = run([])
    assert (missing['status'], missing['reason']) == (
        'UNEVALUABLE', 'NO_SESSION_BARS_IN_ENTRY_WINDOW')
    assert retain(other, observation(), missing)['outcome_status'] == 'UNEVALUABLE'


def test_pending_outcome_is_never_retained(tmp_path):
    pending = run([FILL])
    assert (pending['status'], pending['reason']) == ('PENDING_PATH', 'POSITION_OPEN')
    refused('OUTCOME_NOT_FINAL', retain, tmp_path, observation(), pending)
    assert snapshot(tmp_path) == []


def test_retry_differing_only_in_evaluation_keeps_the_first_outcome(tmp_path):
    assert EVALUATION_FIELDS == ('path_complete_through_at', 'evaluated_at', 'bars_supplied')
    obs = observation()
    first = retain(tmp_path, obs, run([FILL, TARGET], obs=obs))
    before = snapshot(tmp_path)
    assert retain(tmp_path, obs, run([FILL, TARGET], obs=obs))['status'] == 'ALREADY_RETAINED'
    later = run([FILL, TARGET, bar(2, '12', '12.5', '11.8', '12.2')], obs=obs,
                evaluated=COMPLETE + timedelta(minutes=90))
    assert later['bars_supplied'] == 3
    again = retain(tmp_path, obs, later)
    assert again['status'] == 'ALREADY_RETAINED' and again['identity'] == first['identity']
    assert again['evaluated_at'] == first['evaluated_at']
    assert snapshot(tmp_path) == before


def test_a_retained_result_cannot_be_replaced_by_a_re_evaluation(tmp_path):
    obs = observation()
    # Missing bars first; later backfilled bars would turn it into a CLOSED win.
    retain(tmp_path, obs, run([], obs=obs))
    before = snapshot(tmp_path)
    refused('OUTCOME_CONFLICT', retain, tmp_path, obs, run([FILL, TARGET], obs=obs))
    # A held loss cannot be swapped for a different exit either.
    losses = tmp_path / 'losses'
    losses.mkdir(mode=0o700)
    retain(losses, obs, run([FILL, bar(1, '10', '10.2', '8.9', '9.5')], obs=obs))
    refused('OUTCOME_CONFLICT', retain, losses, obs, run([FILL, TARGET], obs=obs))
    assert [entry for entry in snapshot(tmp_path) if entry[1] is not None] == before


def test_assumption_sets_and_markets_are_kept_separate(tmp_path):
    obs = observation()
    retain(tmp_path, obs, run([FILL, TARGET], obs=obs))
    retain(tmp_path, obs, run([FILL, TARGET], obs=obs, assumptions=COSTS))
    zero, costs = load(tmp_path), load(tmp_path, assumptions=COSTS)
    assert len(zero) == len(costs) == 1
    assert zero[0]['assumptions']['assumptions_version'] == 'fixture-zero'
    assert costs[0]['assumptions']['assumptions_version'] == 'fixture-costs'
    assert load(tmp_path, market='US', assumptions=US_ZERO) == ()
    with pytest.raises(ValueError):
        load(tmp_path, market='US', assumptions=ZERO)
    with pytest.raises(ValueError):
        load(tmp_path, market='XX')


@pytest.mark.parametrize('mutate', [
    lambda obs, out: out.update(decision_id='f' * 64),
    lambda obs, out: out.update(live_money=True),
    lambda obs, out: out.update(performance_claim='EDGE'),
    lambda obs, out: out.update(status=['CLOSED']),
    lambda obs, out: out['assumptions'].update(market='US'),
    lambda obs, out: out['assumptions'].update(cost_bps_per_side='-1'),
    lambda obs, out: out.update(extra=True),
    lambda obs, out: obs[1].update(symbol='OTHER'),
])
def test_forged_or_unbound_outcomes_are_rejected_before_any_write(tmp_path, mutate):
    identity, record = observation()
    outcome = run([FILL, TARGET], obs=(identity, record))
    obs = (identity, deepcopy(record))
    outcome = deepcopy(outcome)
    mutate(obs, outcome)
    refused('RECORD_REJECTED', retain, tmp_path, obs, outcome)
    assert snapshot(tmp_path) == []


def test_outcome_evaluated_after_the_store_clock_is_rejected(tmp_path):
    obs = observation()
    outcome = run([FILL, TARGET], obs=obs)
    refused('RECORD_REJECTED', retain, tmp_path, obs, outcome, now=COMPLETE)
    with pytest.raises(TypeError):
        retain_decision_outcome(str(tmp_path), obs, outcome, now=NOW.replace(tzinfo=None))
    assert snapshot(tmp_path) == []


def test_reader_fails_closed_on_damaged_bytes():
    payload = canonical_bytes(run([FILL, TARGET]))
    digest = sha256(payload).hexdigest()
    with pytest.raises(RetainedDecisionOutcomeError):
        read_retained_decision_outcome(payload, expected_sha256='0' * 64)
    spaced = json.dumps(json.loads(payload), indent=1).encode()
    with pytest.raises(RetainedDecisionOutcomeError):
        read_retained_decision_outcome(spaced, expected_sha256=sha256(spaced).hexdigest())
    for forged in ({'status': ['CLOSED']}, {'status': 'PENDING_PATH', 'reason': 'POSITION_OPEN',
                                              'exit': None, 'metrics': None}):
        bad = canonical_bytes({**json.loads(payload), **forged})
        with pytest.raises(RetainedDecisionOutcomeError):
            read_retained_decision_outcome(bad, expected_sha256=sha256(bad).hexdigest())
    assert read_retained_decision_outcome(payload, expected_sha256=digest)['status'] == 'CLOSED'


def test_damaged_or_unexpected_store_content_fails_closed(tmp_path):
    obs = observation()
    retain(tmp_path, obs, run([FILL, TARGET], obs=obs))
    (tmp_path / 'notes.txt').write_text('x')
    refused('STORE_INVALID', load, tmp_path)
    refused('STORE_INVALID', retain, tmp_path, obs, run([FILL, TARGET], obs=obs, assumptions=COSTS))
    (tmp_path / 'notes.txt').unlink()
    # Two retained outcomes for one decision under one assumptions set.
    write_record(tmp_path, run([], obs=obs))
    refused('STORE_INVALID', load, tmp_path)


def test_pending_record_planted_in_the_store_fails_closed(tmp_path):
    write_record(tmp_path, run([FILL]))
    refused('STORE_INVALID', load, tmp_path)


def test_directory_configuration_and_lock(tmp_path):
    obs = observation()
    outcome = run([FILL, TARGET], obs=obs)
    refused('CONFIG_INVALID', retain_decision_outcome, 'relative/dir', obs, outcome, now=NOW)
    refused('DIRECTORY_UNAVAILABLE', retain, tmp_path / 'missing', obs, outcome)
    shared = tmp_path / 'shared'
    shared.mkdir()
    shared.chmod(0o770)
    refused('DIRECTORY_UNSAFE', retain, shared, obs, outcome)
    store = tmp_path / 'store'
    store.mkdir()
    store.chmod(0o700)
    fd = os.open(str(store), os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        refused('STORE_BUSY', retain, store, obs, outcome)
    finally:
        os.close(fd)
    assert snapshot(store) == []
    assert retain(store, obs, outcome)['status'] == 'RETAINED'
