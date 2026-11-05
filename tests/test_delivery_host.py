"""Composition/recovery integration with simulated host components; no sockets."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.delivery_host import ComposedHost
from tools.delivery_runner import DeliveryError, SOCKET_TESTS, atomic_json, deliver, recover

SHA = 'a' * 40
OLD = 'b' * 40
NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def evidence(sha=SHA):
    return dict(commit=sha, live_money=False, http=True, api=True,
                runtime_compatible=True, sig_v1=True, scheduler_target=sha,
                scheduler_verified=True)


@pytest.fixture
def wiring(tmp_path):
    components = []
    events = []
    for name in ('prepared', 'tests', 'runtime', 'candidate'):
        root = tmp_path / name
        root.mkdir()
        receipts, outputs = root / 'receipts', root / 'outputs'
        receipts.mkdir()
        outputs.mkdir()
        component = SimpleNamespace(receipts=receipts, outputs=outputs)
        def run(sha, c=component, n=name):
            events.append(n)
            atomic_json(c.receipts / (sha + '.json'), {'complete': True})
        def verify(sha, c=component):
            value = json.loads((c.receipts / (sha + '.json')).read_text())
            if value != {'complete': True}:
                raise DeliveryError('incomplete component')
            return {**evidence(sha), 'full_suite': True, 'reporting_only': True,
                    'runtime_rss': True, 'socket_tests': dict.fromkeys(SOCKET_TESTS, True)}
        component.prepare = Mock(side_effect=run)
        component.validate = Mock(side_effect=run)
        component.verify = Mock(side_effect=verify)
        components.append(component)
    prepared, tests, runtime, candidate = components
    prepared.bundles = prepared.outputs
    prepared.environments = tmp_path / 'environments'
    prepared.environments.mkdir()
    tests.prepared, runtime.tests, candidate.runtime = prepared, tests, runtime
    backend = Mock()
    backend.assert_contained = Mock()
    backend.quiescent.return_value = True
    backend.snapshot.return_value = dict(commit=OLD, verified=True, schedule='previous schedule')
    backend.production.return_value = evidence()
    backend.rollback_check.return_value = evidence(OLD)
    host = ComposedHost(candidate, backend)
    state = tmp_path / 'state'
    state.mkdir()
    request = dict(schema='delivery-request-v1', request_id='cycle', head=SHA,
                   created_at=NOW.isoformat(), live_money=False, scope='SIG-v1-reporting-only')
    return SimpleNamespace(host=host, backend=backend, components=components,
                           state=state, request=request, events=events)


def run(w):
    return deliver(w.request, w.state, Mock(), w.host, now=NOW)


def test_composed_transaction(wiring):
    w = wiring
    assert run(w)['status'] == 'SUCCEEDED'
    assert w.events == ['prepared', 'tests', 'runtime', 'candidate']
    assert w.components[-1].verify.call_count >= 5
    w.backend.cutover.assert_called_once_with(SHA)
    w.backend.adopt_schedule.assert_called_once_with(SHA)


@pytest.mark.parametrize('index', range(4))
@pytest.mark.parametrize('interruption', [False, True])
def test_incomplete_receipts_survive_recovery(wiring, index, interruption):
    w = wiring
    c = w.components[index]
    def fail(sha):
        atomic_json(c.receipts / (sha + '.json'), {'complete': False})
        raise KeyboardInterrupt() if interruption else DeliveryError('failed gate')
    (c.prepare if index == 0 else c.validate).side_effect = fail
    with pytest.raises((KeyboardInterrupt, DeliveryError)):
        run(w)
    before = (c.receipts / (SHA + '.json')).read_bytes()
    for _ in range(2):
        with pytest.raises(DeliveryError, match='RECOVERY_REQUIRED'):
            recover(w.state, w.host)
        assert (c.receipts / (SHA + '.json')).read_bytes() == before
    w.backend.cutover.assert_not_called()
    assert json.loads((w.state / 'delivery.json').read_text())['status'] == 'RECOVERY_REQUIRED'
    assert w.backend.cleanup.called


@pytest.mark.parametrize('index', range(4))
def test_orphans_rejected_before_preparation(wiring, index):
    w = wiring
    (w.components[index].outputs / SHA).mkdir()
    with pytest.raises(DeliveryError, match='RECOVERY_REQUIRED'):
        run(w)
    assert w.events == []
    w.backend.cutover.assert_not_called()


@pytest.mark.parametrize('operation', ['cutover', 'adopt_schedule', 'production'])
def test_backend_failure_restores_both_states(wiring, operation):
    w = wiring
    getattr(w.backend, operation).side_effect = DeliveryError('host failure')
    with pytest.raises(DeliveryError, match='ROLLED_BACK'):
        run(w)
    snapshot = w.backend.snapshot.return_value
    w.backend.restore_release.assert_called_once_with(snapshot)
    w.backend.restore_schedule.assert_called_once_with(snapshot)


@pytest.mark.parametrize('value', [False, None, 1])
def test_uncertain_containment_latches_recovery(wiring, value):
    w = wiring
    w.backend.quiescent.return_value = value
    with pytest.raises(DeliveryError, match='RECOVERY_REQUIRED'):
        run(w)
    with pytest.raises(DeliveryError, match='RECOVERY_REQUIRED'):
        recover(w.state, w.host)


def test_tamper_after_snapshot_prevents_cutover(wiring):
    w = wiring
    def snapshot():
        atomic_json(w.components[-1].receipts / (SHA + '.json'), {'complete': False})
        return dict(commit=OLD, verified=True, schedule='previous schedule')
    w.backend.snapshot.side_effect = snapshot
    with pytest.raises(DeliveryError, match='RECOVERY_REQUIRED'):
        run(w)
    w.backend.cutover.assert_not_called()
    w.backend.restore_release.assert_called_once()
    w.backend.restore_schedule.assert_called_once()


def test_restart_complete_prefix_can_be_cleaned(wiring):
    w = wiring
    w.components[0].prepare(SHA)
    w.host.cleanup(SHA)
    assert (w.components[0].receipts / (SHA + '.json')).exists()


def test_downstream_receipt_without_predecessor_rejected(wiring):
    w = wiring
    w.components[-1].validate(SHA)
    with pytest.raises(DeliveryError, match='noncontiguous'):
        w.host.cleanup(SHA)


@pytest.mark.parametrize('operation', ['adopt_schedule', 'production', 'cutover'])
def test_mutations_require_accepted_receipt(wiring, operation):
    with pytest.raises(FileNotFoundError):
        getattr(wiring.host, operation)(SHA)
    getattr(wiring.backend, operation).assert_not_called()


def test_live_money_violation_rolls_back(wiring):
    wiring.backend.production.return_value['live_money'] = 0
    with pytest.raises(DeliveryError, match='ROLLED_BACK'):
        run(wiring)


def test_schedule_restore_failure_remains_recoverable(wiring):
    w = wiring
    w.backend.production.side_effect = DeliveryError('HTTP failure')
    w.backend.restore_schedule.side_effect = DeliveryError('cron failure')
    with pytest.raises(DeliveryError, match='RECOVERY_REQUIRED'):
        run(w)
    w.backend.restore_schedule.side_effect = None
    assert recover(w.state, w.host)['status'] == 'ROLLED_BACK'


def test_containment_required_before_artifact_creation(wiring):
    w = wiring
    w.backend.assert_contained.side_effect = DeliveryError('containment unavailable')
    with pytest.raises(DeliveryError, match='containment unavailable'):
        run(w)
    assert w.events == []
    w.backend.cutover.assert_not_called()


@pytest.mark.parametrize('sha', ['../escape', '', None, 'a' * 39])
def test_invalid_identity_never_reaches_backend(wiring, sha):
    with pytest.raises(DeliveryError, match='full SHA'):
        wiring.host.prepare(sha)
    wiring.backend.assert_contained.assert_not_called()


def test_dangling_artifact_link_is_not_absence(wiring):
    w = wiring
    (w.components[-1].outputs / SHA).symlink_to(w.state / 'absent')
    with pytest.raises(DeliveryError, match='RECOVERY_REQUIRED'):
        run(w)
    assert w.events == []


@pytest.mark.parametrize('index', range(4))
def test_composed_job_recovery_keeps_uncertain_stage_receipt(wiring, index):
    """Shared cleanup cannot turn incomplete admission evidence into acceptance."""
    import os
    import sys
    from pathlib import Path
    from unittest.mock import patch
    from tools.delivery_composition import ALL_JOBS, RELEASE_JOBS, HostJobs, compose_supervisor
    from tools.delivery_jobs import JobContainment
    from tools.delivery_process import CleanupError

    w = wiring
    containment = JobContainment({k: '/temporary/' + k for k in ALL_JOBS}, owner=os.getuid())
    jobs = HostJobs(containment, python=Path(sys.executable).resolve(),
                   persistent_groups=['/serving'], isolation=lambda: True)
    for component in w.components:
        component.launcher = None
    supervisor = compose_supervisor(w.state, Mock(), w.components[-1], w.backend, jobs)
    w.backend.cleanup.side_effect = w.backend.services._cleanup
    w.backend.quiescent.side_effect = w.backend.services._quiescent
    component = w.components[index]
    def fail(sha):
        atomic_json(component.receipts / (sha + '.json'), {'complete': False})
        raise CleanupError('admission or descendants uncertain')
    (component.prepare if index == 0 else component.validate).side_effect = fail
    def cleanup(key):
        if key == RELEASE_JOBS[index]:
            raise CleanupError('descendant unresolved')
    with patch('tools.delivery_composition._protected'), \
            patch.object(containment, 'quiescent', return_value=True), \
            patch.object(containment, 'cleanup', side_effect=cleanup) as clean:
        with pytest.raises(DeliveryError):
            supervisor.release(w.request, now=NOW)
        assert {c.args[0] for c in clean.call_args_list} == set(RELEASE_JOBS)
        before = (component.receipts / (SHA + '.json')).read_bytes()
        with pytest.raises(DeliveryError, match='RECOVERY_REQUIRED'):
            supervisor.startup()
        # Even eventual kernel cleanup cannot bless the incomplete receipt.
        clean.side_effect = None
        with pytest.raises(DeliveryError, match='RECOVERY_REQUIRED'):
            supervisor.startup()
        assert (component.receipts / (SHA + '.json')).read_bytes() == before
    w.backend.cutover.assert_not_called()
