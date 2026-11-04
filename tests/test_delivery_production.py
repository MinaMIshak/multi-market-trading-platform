"""Filesystem routing is real; host services and schedules are simulated."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.delivery_production import ProductionBackend
from tools.delivery_runner import DeliveryError, atomic_json

NEW, OLD = 'a' * 40, 'b' * 40


def evidence(sha):
    return dict(commit=sha, live_money=False, http=True, api=True,
                runtime_compatible=True, sig_v1=True)


@pytest.fixture
def w(tmp_path):
    state, routing = tmp_path / 'state', tmp_path / 'routing'
    state.mkdir()
    routing.mkdir()
    route = routing / 'current.json'
    atomic_json(route, dict(commit=OLD, live_money=False))
    host = Mock()
    host.contained.return_value = True
    host.stage.side_effect = evidence
    host.observe.side_effect = evidence
    schedule = [OLD + '/venv/bin/python nightly']
    host.read_schedule.side_effect = lambda: schedule[0]
    host.install_schedule.side_effect = lambda text: schedule.__setitem__(0, text)
    host.release_schedule.side_effect = lambda sha: sha + '/venv/bin/python nightly'
    host.verify_schedule.side_effect = lambda sha, text: text == sha + '/venv/bin/python nightly'
    host.validation_quiescent.return_value = True
    backend = ProductionBackend(state, route, host)
    return SimpleNamespace(b=backend, host=host, schedule=schedule, route=route)


def test_cutover_and_idempotent_restore(w):
    snapshot = w.b.snapshot()
    w.b.cutover(NEW)
    w.b.adopt_schedule(NEW)
    assert w.b.production(NEW)['scheduler_target'] == NEW
    # Recovery survives a new backend instance.
    w.b = ProductionBackend(w.b.state, w.route, w.host)
    for _ in range(2):
        w.b.restore_release(snapshot)
        w.b.restore_schedule(snapshot)
        assert w.b.rollback_check(snapshot)['commit'] == OLD


@pytest.mark.parametrize('gate', ['http', 'api', 'runtime_compatible', 'sig_v1', 'live_money'])
def test_failed_staging_preserves_old_route_and_schedule(w, gate):
    snapshot = w.b.snapshot()
    bad = evidence(NEW)
    bad[gate] = True if gate == 'live_money' else False
    w.host.stage.side_effect = lambda sha: bad
    with pytest.raises(DeliveryError):
        w.b.cutover(NEW)
    assert json.loads(w.route.read_text())['commit'] == OLD
    assert w.schedule[0] == snapshot['schedule']


@pytest.mark.parametrize('change', ['route', 'schedule'])
def test_concurrent_state_change_rejected(w, change):
    w.b.snapshot()
    if change == 'route':
        atomic_json(w.route, dict(commit='c' * 40, live_money=False))
    else:
        w.schedule[0] = 'operator change'
    with pytest.raises(DeliveryError):
        w.b.cutover(NEW)
    w.host.stage.assert_not_called()


def test_staging_race_rejected(w):
    w.b.snapshot()
    def stage(sha):
        w.schedule[0] = 'changed'
        return evidence(sha)
    w.host.stage.side_effect = stage
    with pytest.raises(DeliveryError, match='during staging'):
        w.b.cutover(NEW)
    assert json.loads(w.route.read_text())['commit'] == OLD


def test_post_cutover_failure_restorable(w):
    snapshot = w.b.snapshot()
    w.host.observe.side_effect = lambda sha: {**evidence(sha), 'http': sha == OLD}
    with pytest.raises(DeliveryError):
        w.b.cutover(NEW)
    assert json.loads(w.route.read_text())['commit'] == NEW
    w.b.restore_release(snapshot)
    w.b.restore_schedule(snapshot)
    assert w.b.rollback_check(snapshot)['commit'] == OLD


def test_partial_schedule_install_restorable(w):
    snapshot = w.b.snapshot()
    w.b.cutover(NEW)
    def partial(text):
        w.schedule[0] = 'partial'
        raise OSError('install interrupted')
    w.host.install_schedule.side_effect = partial
    with pytest.raises(OSError):
        w.b.adopt_schedule(NEW)
    w.host.install_schedule.side_effect = lambda text: w.schedule.__setitem__(0, text)
    w.b.restore_schedule(snapshot)
    assert w.schedule[0] == snapshot['schedule']


@pytest.mark.parametrize('value', [None, 1, False])
def test_containment_must_be_explicit(w, value):
    w.host.contained.return_value = value
    with pytest.raises(DeliveryError):
        w.b.snapshot()
    assert not w.b.backup.exists()


def test_backup_tamper_rejects_restore(w):
    snapshot = w.b.snapshot()
    atomic_json(w.b.backup, {**snapshot, 'schedule': 'injected'})
    with pytest.raises(DeliveryError):
        w.b.restore_schedule(snapshot)
    w.host.install_schedule.assert_not_called()


def test_symlink_route_rejected(w):
    other = w.route.parent / 'other'
    w.route.rename(other)
    w.route.symlink_to(other)
    with pytest.raises(DeliveryError):
        w.b.snapshot()


def test_schedule_requires_local_interpreter(w):
    w.b.snapshot()
    w.b.cutover(NEW)
    w.host.release_schedule.side_effect = None
    w.host.release_schedule.return_value = '/worktree/.venv/bin/python nightly'
    with pytest.raises(DeliveryError):
        w.b.adopt_schedule(NEW)
    w.host.install_schedule.assert_not_called()


def test_cleanup_never_stops_serving_release(w):
    w.b.cleanup(NEW)
    w.host.cleanup_validation.assert_called_once_with(NEW)
    w.host.stage.assert_not_called()
    assert json.loads(w.route.read_text())['commit'] == OLD


def test_uncertain_cleanup_fails_closed(w):
    w.host.validation_quiescent.return_value = None
    with pytest.raises(DeliveryError):
        w.b.cleanup(NEW)


@pytest.mark.parametrize('failure', ['post_route', 'schedule', 'post_schedule', 'interrupt'])
def test_delivery_transaction_recovers_real_pointer(w, tmp_path, failure):
    from datetime import datetime, timezone
    from tools.delivery_runner import SOCKET_TESTS, deliver, recover
    state = tmp_path / 'transaction'
    state.mkdir()
    now = datetime.now(timezone.utc)
    request = dict(schema='delivery-request-v1', request_id='cycle', head=NEW,
                   created_at=now.isoformat(), live_money=False, scope='SIG-v1-reporting-only')
    host = Mock(wraps=w.b)
    host.prepare = Mock()
    host.validate = Mock(return_value=dict(commit=NEW, full_suite=True, reporting_only=True,
                                          runtime_rss=True, socket_tests=dict.fromkeys(SOCKET_TESTS, True)))
    host.candidate = Mock(return_value=evidence(NEW))
    if failure == 'post_route':
        w.host.observe.side_effect = lambda sha: {**evidence(sha), 'http': sha == OLD}
    elif failure == 'schedule':
        def install(text):
            w.schedule[0] = text
            if text.startswith(NEW):
                raise OSError('failed after installing')
        w.host.install_schedule.side_effect = install
    elif failure == 'post_schedule':
        host.production.side_effect = DeliveryError('failed production gate')
    else:
        def interrupted(sha):
            w.b.cutover(sha)
            raise KeyboardInterrupt()
        host.cutover.side_effect = interrupted
    with pytest.raises((DeliveryError, KeyboardInterrupt)):
        deliver(request, state, Mock(), host, now=now)
    if failure == 'interrupt':
        # Fresh backend recovers without any in-memory snapshot.
        host = ProductionBackend(w.b.state, w.route, w.host)
        assert recover(state, host)['status'] == 'ROLLED_BACK'
    assert json.loads((state / 'delivery.json').read_text())['status'] == 'ROLLED_BACK'
    assert json.loads(w.route.read_text())['commit'] == OLD
    assert w.schedule[0].startswith(OLD)


def test_route_write_failure_leaves_backup_recoverable(w, monkeypatch):
    from tools import delivery_production
    snapshot = w.b.snapshot()
    real = delivery_production.atomic_json
    monkeypatch.setattr(delivery_production, 'atomic_json', Mock(side_effect=OSError('disk failure')))
    with pytest.raises(OSError):
        w.b.cutover(NEW)
    assert json.loads(w.route.read_text())['commit'] == OLD
    monkeypatch.setattr(delivery_production, 'atomic_json', real)
    w.b.restore_release(snapshot)
    assert w.b.rollback_check(snapshot)['commit'] == OLD
