"""Artificial retained evidence only; no market coverage, outcome or edge evidence."""
from datetime import timedelta
from hashlib import sha256
import json

import pytest

from app.decision_observation import canonical_bytes, decision_observation
from app.decision_observation_producer import (
    CONTRACT_VERSION, DECISION_STATUSES, PRODUCTION_REASONS,
    DecisionObservationProductionError, produce_egx_decision_observations)
from app.decision_observation_store import load_retained_decision_observations
from app.scanner_v2_retained_binding import bind_retained_egx_rank_report
from tests.test_scanner_v2_card_report import VIEW, evidence, project
from tests.test_scanner_v2_egx_rank import NEXT_CLOSE, record, report
from tests.test_scanner_v2_retained_binding import OPEN

NOW = VIEW + timedelta(minutes=5)
SAFETY = {'event_context_source': 'NOT_CONFIGURED', 'event_context': 'NOT_ATTACHED',
          'outcome_status': 'NOT_OBSERVED', 'performance_claim': 'NONE', 'mode': 'SHADOW',
          'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The store refuses group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


def produce(directory, now=NOW, artifact=None, ev=None, expected=None):
    artifact = artifact if artifact is not None else report()
    return produce_egx_decision_observations(
        str(directory), artifact, expected_sha256=expected or sha256(artifact).hexdigest(),
        evidence=ev or evidence(), now=now)


def decisions(artifact=None):
    artifact = artifact if artifact is not None else report()
    return bind_retained_egx_rank_report(
        artifact, expected_sha256=sha256(artifact).hexdigest(), evidence=evidence())['decisions']


def load(directory):
    return load_retained_decision_observations(str(directory), market='EGX')


def snapshot(directory):
    return sorted((p.name, p.read_bytes()) for p in directory.iterdir())


def refused(reason, *args, **kwargs):
    assert reason in PRODUCTION_REASONS
    with pytest.raises(DecisionObservationProductionError) as info:
        produce(*args, **kwargs)
    assert info.value.reason == reason
    return info.value


def test_open_window_decisions_are_retained_as_exact_observations(tmp_path):
    out = produce(tmp_path)
    assert out['schema_version'] == CONTRACT_VERSION == 'decision-observation-production-v1'
    assert {k: out[k] for k in SAFETY} == SAFETY
    assert out['market'] == 'EGX' and out['rank_version'] == 'EGX-RANK-v1'
    assert out['ranking_artifact_sha256'] == sha256(report()).hexdigest()
    assert out['produced_at'] == NOW.isoformat().replace('+00:00', 'Z')
    assert out['requested'] == 5 and out['candidate_count'] == 2 and out['excluded'] == 3
    assert sum(out['exclusion_counts'].values()) == 3
    assert out['status_counts'] == {'RETAINED': 2, 'ALREADY_RETAINED': 0, 'WINDOW_CLOSED': 0}
    assert tuple(out['status_counts']) == DECISION_STATUSES
    assert [e['symbol'] for e in out['decisions']] == ['AAAA', 'BBBB']
    expected = {}
    for d in decisions():
        payload = canonical_bytes(decision_observation(d, None, recorded_at=NOW))
        expected[sha256(payload).hexdigest()] = payload
    assert {e['identity'] for e in out['decisions']} == set(expected)
    assert snapshot(tmp_path) == sorted((f'{k}.json', v) for k, v in expected.items())
    loaded = load(tmp_path)
    assert [i for i, _ in loaded] == sorted(expected)
    for entry in out['decisions']:
        held = dict(loaded)[entry['identity']]
        assert held['decision_id'] == entry['decision_id']
        assert held['event_context']['status'] == 'NOT_ATTACHED'
        assert held['outcome_status'] == 'NOT_OBSERVED' and held['live_money'] is False
        assert entry['strategy_id'] == held['strategy_id'] == 'EGX-RANK-v1'
        assert entry['strategy_version'] == held['strategy_version'] == '1'
        assert entry['recorded_at'] == held['recorded_at']
    json.dumps(out, allow_nan=False)


def test_rerun_keeps_first_observation_and_writes_nothing(tmp_path):
    first = produce(tmp_path)
    before = snapshot(tmp_path)
    again = produce(tmp_path, now=NOW + timedelta(minutes=30))
    assert snapshot(tmp_path) == before
    assert again['status_counts'] == {'RETAINED': 0, 'ALREADY_RETAINED': 2, 'WINDOW_CLOSED': 0}
    assert [e['identity'] for e in again['decisions']] == [e['identity'] for e in first['decisions']]
    assert [e['recorded_at'] for e in again['decisions']] == \
        [e['recorded_at'] for e in first['decisions']]


@pytest.mark.parametrize('now', [OPEN, OPEN + timedelta(hours=1), NEXT_CLOSE])
def test_closed_window_is_reported_per_decision_and_never_backfilled(tmp_path, now):
    out = produce(tmp_path, now=now)
    assert snapshot(tmp_path) == []
    assert out['status_counts'] == {'RETAINED': 0, 'ALREADY_RETAINED': 0, 'WINDOW_CLOSED': 2}
    assert [e['decision_id'] for e in out['decisions']] == [d.identity for d in decisions()]
    for entry in out['decisions']:
        assert entry['status'] == 'WINDOW_CLOSED'
        assert entry['identity'] is None and entry['recorded_at'] is None


def test_last_instant_before_entry_window_is_still_recorded(tmp_path):
    out = produce(tmp_path, now=OPEN - timedelta(microseconds=1))
    assert out['status_counts']['RETAINED'] == 2


def test_closed_window_after_retention_leaves_first_records(tmp_path):
    produce(tmp_path)
    before = snapshot(tmp_path)
    late = produce(tmp_path, now=OPEN)
    assert snapshot(tmp_path) == before
    assert late['status_counts']['WINDOW_CLOSED'] == 2


def test_clock_before_ranking_generation_refused_without_writes(tmp_path):
    refused('CLOCK_BEFORE_GENERATION', tmp_path, now=VIEW - timedelta(microseconds=1))
    assert snapshot(tmp_path) == []


def test_unverified_or_forged_evidence_refused_without_writes(tmp_path):
    refused('BINDING_REJECTED', tmp_path, expected='0' * 64)
    refused('BINDING_REJECTED', tmp_path, ev=evidence().model_copy(
        update={'calendar_admission': 'ADMITTED'}))
    with pytest.raises(DecisionObservationProductionError) as info:
        produce_egx_decision_observations(
            str(tmp_path), report(), expected_sha256=sha256(report()).hexdigest(),
            evidence=evidence().binding, now=NOW)
    assert info.value.reason == 'BINDING_REJECTED'
    refused('BINDING_REJECTED', tmp_path, artifact=b'{"not": "a ranking"}')
    assert snapshot(tmp_path) == []


def test_invalid_configuration_and_clock(tmp_path):
    with pytest.raises(DecisionObservationProductionError) as info:
        produce_egx_decision_observations(
            'relative/dir', report(), expected_sha256=sha256(report()).hexdigest(),
            evidence=evidence(), now=NOW)
    assert info.value.reason == 'CONFIG_INVALID'
    with pytest.raises(TypeError):
        produce(tmp_path, now=NOW.replace(tzinfo=None))
    with pytest.raises(TypeError):
        produce(tmp_path, now=NOW.isoformat())
    assert snapshot(tmp_path) == []


def test_store_refusal_fails_closed_with_store_reason(tmp_path):
    missing = tmp_path / 'missing'
    error = refused('RETENTION_FAILED', missing)
    assert 'DIRECTORY_UNAVAILABLE' in str(error.detail)
    unsafe = tmp_path / 'unsafe'
    unsafe.mkdir()
    unsafe.chmod(0o777)
    error = refused('RETENTION_FAILED', unsafe)
    assert 'DIRECTORY_UNSAFE' in str(error.detail)
    assert list(unsafe.iterdir()) == []


def test_ranking_without_candidates_produces_no_observations(tmp_path):
    artifact = report([record('AAAA', 'WATCHLIST'), record('BBBB', 'NO_TRADE')])
    out = produce(tmp_path, artifact=artifact)
    assert out['decisions'] == [] and out['candidate_count'] == 0 and out['excluded'] == 2
    assert out['status_counts'] == {s: 0 for s in DECISION_STATUSES}
    assert snapshot(tmp_path) == []


def test_production_is_deterministic_and_leaves_cards_unchanged(tmp_path):
    cards_before = project(observed_at=NOW)
    first, second = tmp_path / 'a', tmp_path / 'b'
    for directory in (first, second):
        directory.mkdir()
        directory.chmod(0o700)
    a, b = produce(first), produce(second)
    assert a == b and snapshot(first) == snapshot(second)
    assert project(observed_at=NOW) == cards_before
