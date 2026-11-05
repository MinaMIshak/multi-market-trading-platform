"""Synthetic host attestations, never runtime evidence."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.delivery_status import (OBSERVATIONS, observation_records, project_delivery,
                                 public_receipt, load_delivery_status)
from app.ui.system import render_delivery
from tests.test_delivery_status import receipt, HEAD, SECRET
from tests.test_delivery_publication import setup

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def observations():
    return dict(schema='delivery-observations-v1', **{
        key: dict(status='SUCCEEDED' if key == 'latest_codex_cycle' else 'COMPLETED',
                  observed_at=NOW.isoformat(), source_head=HEAD,
                  evidence_sha256='c' * 64, live_money=False, private=SECRET)
        for key in OBSERVATIONS})


@pytest.mark.parametrize('key', OBSERVATIONS)
@pytest.mark.parametrize('offset,freshness', [(0, 'RECENT'), (1, 'STALE'), (-1, 'FUTURE')])
def test_freshness_bounds_and_no_health_inference(key, offset, freshness):
    obs = observations()
    seconds = OBSERVATIONS[key][1] + offset if offset >= 0 else -1
    obs[key]['observed_at'] = (NOW - timedelta(seconds=seconds)).isoformat()
    raw = receipt() | dict(observations=obs)
    view = project_delivery(raw, now=NOW)
    assert view[key]['freshness'] == freshness
    assert view['runtime_verified'] is False
    assert view['scheduler_health'] is None
    assert SECRET not in json.dumps(view)
    assert project_delivery(public_receipt(raw), now=NOW) == view


@pytest.mark.parametrize('field,value', [
    ('status', []), ('status', 'GREEN'), ('source_head', 'abc'),
    ('evidence_sha256', 'z' * 64), ('evidence_sha256', None),
    ('observed_at', None), ('observed_at', '2026-11-05'),
    ('observed_at', 'bad'), ('live_money', True), ('live_money', 0),
])
def test_invalid_record_is_unknown_without_hiding_other_records(field, value):
    obs = observations()
    obs['latest_codex_cycle'][field] = value
    view = project_delivery(receipt() | dict(observations=obs), now=NOW)
    assert view['latest_codex_cycle'] is None
    assert view['us_chain_status'] is not None


def test_observation_cannot_satisfy_missing_deployment_gates():
    raw = receipt() | dict(observations=observations())
    raw.pop('tests')
    view = project_delivery(raw, now=NOW)
    assert view['deployed_release'] is None
    assert view['deployment_result'] == 'UNVERIFIED_TERMINAL_RECEIPT'
    assert view['latest_codex_cycle']['status'] == 'SUCCEEDED'


def test_publication_requires_separate_protected_sidecar(setup, monkeypatch):
    publisher, journal, destination = setup
    raw = receipt() | dict(observations=observations())
    journal.write_text(json.dumps(raw))
    assert 'observations' not in publisher.publish()
    sidecar = publisher.state / 'observations.json'
    sidecar.write_text(json.dumps(observations()))
    sidecar.chmod(0o600)
    published = publisher.publish()
    assert observation_records(published['observations']) == observation_records(observations())
    monkeypatch.setenv('EGX_DELIVERY_RECEIPT', str(destination))
    view = load_delivery_status()
    assert view['latest_claude_review']['source_head'] == HEAD
    assert SECRET not in render_delivery({'delivery': view})
    sidecar.unlink()
    assert 'observations' not in publisher.publish()


@pytest.mark.parametrize('kind', ['invalid', 'duplicate', 'oversized', 'unsafe', 'symlink'])
def test_bad_sidecar_clears_observations_preserves_delivery(setup, kind):
    publisher, _, destination = setup
    path = publisher.state / 'observations.json'
    path.write_text(json.dumps(observations()))
    path.chmod(0o600)
    publisher.publish()
    if kind == 'invalid':
        path.write_text('[]')
    elif kind == 'duplicate':
        path.write_text('{"schema":1,"schema":2}')
    elif kind == 'oversized':
        path.write_text('x' * 1048577)
    elif kind == 'unsafe':
        path.chmod(0o666)
    else:
        path.unlink()
        path.symlink_to(destination)
    result = publisher.publish()
    assert 'observations' not in result
    assert project_delivery(result)['deployed_release'] == HEAD


def test_republication_does_not_refresh_observation_time(setup):
    publisher, _, _ = setup
    path = publisher.state / 'observations.json'
    path.write_text(json.dumps(observations()))
    path.chmod(0o600)
    first = publisher.publish()
    second = publisher.publish()
    assert first == second
    later = project_delivery(second, now=NOW + timedelta(days=8))
    assert all(later[key]['freshness'] == 'STALE' for key in OBSERVATIONS)
