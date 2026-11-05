"""Offline composition tests; no kernel isolation or deployment evidence."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tools.delivery_composition import ALL_JOBS, RELEASE_JOBS, HostJobs, compose_supervisor
from tools.delivery_jobs import JobContainment
from tools.delivery_process import CleanupError
from tools.delivery_runner import DeliveryError

SHA = 'a' * 40


@pytest.fixture
def jobs():
    containment = JobContainment({k: '/temporary/' + k for k in ALL_JOBS}, owner=os.getuid())
    return HostJobs(containment, python=Path(sys.executable).resolve(),
                    persistent_groups=['/production', '/rollback'], isolation=lambda: True)


@pytest.fixture
def composition(jobs, tmp_path):
    prepared = SimpleNamespace(launcher=None)
    tests = SimpleNamespace(prepared=prepared, launcher=None)
    runtime = SimpleNamespace(tests=tests, launcher=None)
    candidate = SimpleNamespace(runtime=runtime, launcher=None)
    production = SimpleNamespace(services=SimpleNamespace())
    supervisor = compose_supervisor(tmp_path, None, candidate, production, jobs)
    return supervisor, (prepared, tests, runtime, candidate), production.services


def test_shared_binding_and_preflight(composition, jobs):
    supervisor, components, services = composition
    assert all(c.launcher is supervisor.launcher is jobs.launcher for c in components)
    assert services._cleanup == jobs.cleanup
    assert services._quiescent == jobs.quiescent
    with patch('tools.delivery_composition._protected'), \
            patch.object(jobs.containment, 'quiescent', return_value=True) as observe:
        with supervisor.writer_session():
            assert observe.call_count == len(ALL_JOBS)


@pytest.mark.parametrize('key', ALL_JOBS)
def test_busy_stage_prevents_work(composition, jobs, key):
    supervisor, _, _ = composition
    with patch('tools.delivery_composition._protected'), \
            patch.object(jobs.containment, 'quiescent', side_effect=lambda k: k != key):
        with pytest.raises(DeliveryError, match='still active'):
            with supervisor.writer_session():
                pytest.fail('writer admitted')
        with patch('tools.delivery_supervisor.deliver') as deliver:
            with pytest.raises(DeliveryError):
                supervisor.release({})
            deliver.assert_not_called()


@pytest.mark.parametrize('key', RELEASE_JOBS)
def test_uncertain_cleanup_attempts_every_stage(composition, jobs, key):
    _, _, services = composition
    def cleanup(k):
        if k == key:
            raise CleanupError('descendant unresolved')
    with patch.object(jobs.containment, 'cleanup', side_effect=cleanup) as clean:
        with pytest.raises(CleanupError, match='aggregate'):
            services._cleanup(SHA)
    assert [c.args[0] for c in clean.call_args_list] == list(RELEASE_JOBS)


@pytest.mark.parametrize('key', RELEASE_JOBS)
def test_population_in_any_stage_is_not_quiescence(jobs, key):
    with patch.object(jobs.containment, 'quiescent', side_effect=lambda k: k != key):
        assert jobs.quiescent(SHA) is False


@pytest.mark.parametrize('kind', ['missing', 'overlap', 'persistent', 'drift'])
def test_invalid_policy(jobs, kind):
    if kind == 'missing':
        del jobs.containment.policies['tests']
    elif kind == 'overlap':
        jobs.containment.policies['tests'] = '/temporary/runtime/child'
        jobs._policy = dict(jobs.containment.policies)
    elif kind == 'persistent':
        jobs.persistent_groups = ('/temporary',)
    else:
        jobs.containment.policies['tests'] = '/different'
    with pytest.raises(DeliveryError):
        jobs.validate_policy()


@pytest.mark.parametrize('result', [False, None, 1])
def test_isolation_requires_explicit_truth(jobs, result):
    jobs.isolation = lambda: result
    with patch('tools.delivery_composition._protected'):
        with pytest.raises(DeliveryError, match='isolation'):
            jobs.preflight()


def test_binding_drift_rejected(composition):
    supervisor, components, _ = composition
    components[2].launcher = Mock()
    with patch('tools.delivery_composition._protected'):
        with pytest.raises(DeliveryError, match='composition changed'):
            with supervisor.writer_session():
                pytest.fail('writer admitted')


def test_recovery_precedes_preflight(composition):
    supervisor, _, _ = composition
    order = []
    with patch.object(supervisor, '_recover', side_effect=lambda: order.append('recover')), \
            patch.object(supervisor, 'preflight', side_effect=lambda: order.append('preflight')), \
            patch('tools.delivery_supervisor.deliver', side_effect=lambda *a, **k: order.append('deliver')):
        supervisor.release({})
    assert order == ['recover', 'preflight', 'deliver']


def test_writer_latch_remains_when_recovery_uncertain(composition):
    supervisor, _, _ = composition
    marker = supervisor.state / 'writer-active.json'
    marker.write_text('{}')
    with patch.object(supervisor, 'preflight') as preflight:
        with pytest.raises(DeliveryError, match='reconciliation'):
            supervisor.release({})
        preflight.assert_not_called()
    assert marker.exists()


def test_conflicting_launcher_rejected_without_partial_rebinding(composition, jobs):
    supervisor, components, services = composition
    for component in components:
        component.launcher = None
    components[-1].launcher = Mock()
    previous_cleanup = services._cleanup
    with pytest.raises(DeliveryError, match='conflicting'):
        compose_supervisor(supervisor.state, None, components[-1], supervisor.host.backend, jobs)
    assert all(c.launcher is None for c in components[:-1])
    assert services._cleanup == previous_cleanup


def test_supervisor_launcher_drift_rejected(composition):
    supervisor, _, _ = composition
    supervisor.launcher = Mock()
    with patch('tools.delivery_composition._protected'):
        with pytest.raises(DeliveryError, match='composition changed'):
            with supervisor.writer_session():
                pytest.fail('writer admitted')
