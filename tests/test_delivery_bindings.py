"""Composed filesystem schedule/routing; injected HTTP/systemd evidence only."""
import json
from unittest.mock import Mock

import pytest

from tools.delivery_bindings import ProductionServices
from tools.delivery_production import ProductionBackend
from tools.delivery_runner import DeliveryError, atomic_json
from tools.delivery_schedule import CronJob, ReleaseSchedule
from tests.test_delivery_service import SHA, service

OLD = 'b' * 40


@pytest.fixture
def composed(tmp_path, service):
    envs, bundles, cron, state, routing = [tmp_path / name for name in
                                         ('envs', 'bundles', 'cron', 'state', 'routing')]
    for path in (envs, bundles, cron, state, routing):
        path.mkdir()
    for sha in (OLD, SHA):
        python = envs / sha / 'venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('fixture only')
        python.chmod(0o755)
        (bundles / sha / 'source').mkdir(parents=True)
    schedule = ReleaseSchedule(cron / 'egx', envs, bundles, 'egx',
                               [CronJob(1, 2, module='tools.fixture')], lambda sha: True)
    schedule.target.write_text(schedule.release_schedule(OLD))
    schedule.target.chmod(0o644)
    # Reuse the real service policy/parser with separately pinned old/new units.
    import copy
    policy = copy.deepcopy(service.policies[SHA])
    fragment = tmp_path / f'egx-release-{OLD}.service'
    fragment.write_text('host-reviewed unit fixture')
    policy['fragment'] = str(fragment)
    policy['properties']['FragmentPath'] = str(fragment)
    policy['properties']['Environment'] = f'LIVE_MONEY=false EGX_BUILD_REVISION={OLD}'
    service.policies[OLD] = policy
    def command(*args):
        if args[0] == 'start':
            return ''
        sha = args[-1].removeprefix('egx-release-').removesuffix('.service')
        observed = {**service.observed, **service.policies[sha]['properties']}
        return ''.join(f'{k}={v}\n' for k, v in observed.items())
    service._command.side_effect = command
    def evidence(sha):
        return dict(commit=sha, live_money=False, http=True, api=True,
                    runtime_compatible=True, sig_v1=sha == SHA)
    service.probe.side_effect = evidence
    public = Mock(side_effect=evidence)
    cleanup, quiet = Mock(), Mock(return_value=True)
    bindings = ProductionServices(service, schedule, public, cleanup, quiet)
    pointer = routing / 'current.json'
    atomic_json(pointer, dict(commit=OLD, live_money=False))
    return bindings, ProductionBackend(state, pointer, bindings), service


def test_composed_cutover_schedule_and_restart_rollback(composed):
    bindings, backend, service = composed
    snapshot = backend.snapshot()
    backend.cutover(SHA)
    backend.adopt_schedule(SHA)
    assert backend.production(SHA)['scheduler_target'] == SHA
    assert str(bindings.schedule.environments / SHA) in bindings.read_schedule()
    backend = ProductionBackend(backend.state, backend.route, bindings)
    backend.restore_release(snapshot)
    backend.restore_schedule(snapshot)
    assert backend.rollback_check(snapshot)['commit'] == OLD
    assert bindings.read_schedule() == snapshot['schedule']
    assert all(c.args[0] in ('start', 'show') for c in service._command.call_args_list)


@pytest.mark.parametrize('gate', ['commit', 'http', 'api', 'live_money', 'runtime_compatible', 'sig_v1'])
def test_public_failure_requires_rollback(composed, gate):
    bindings, backend, service = composed
    snapshot = backend.snapshot()
    good = bindings.public_probe.side_effect
    def probe(sha):
        result = good(sha)
        if sha == SHA:
            result[gate] = True if gate == 'live_money' else False
        return result
    bindings.public_probe.side_effect = probe
    with pytest.raises(DeliveryError):
        backend.cutover(SHA)
    assert json.loads(backend.route.read_text())['commit'] == SHA
    backend.restore_release(snapshot)
    backend.restore_schedule(snapshot)
    assert backend.rollback_check(snapshot)['commit'] == OLD


def test_public_probe_drift_rejected(composed):
    bindings, backend, service = composed
    good = bindings.public_probe.side_effect
    def drift(sha):
        service.verify_isolation.return_value = False
        return good(sha)
    bindings.public_probe.side_effect = drift
    with pytest.raises(DeliveryError):
        backend.snapshot()
    assert not backend.backup.exists()


def test_router_uses_read_only_direct_observer(composed, monkeypatch):
    bindings, backend, service = composed
    router = bindings.router(backend.route, {OLD: 8500, SHA: 8501}, ['/health'])
    monkeypatch.setattr(router, '_target', lambda: OLD)
    monkeypatch.setattr(router, '_fetch', lambda sha, path: (b'healthy', 'text/plain'))
    start = Mock()
    assert router({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/health'}, start) == [b'healthy']
    assert start.call_args.args[0] == '200 OK'
    bindings.public_probe.assert_not_called()
    assert all(c.args[0] == 'show' for c in service._command.call_args_list)
    service.observed['ActiveState'] = 'inactive'
    assert router({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/health'}, start) == [b'Release unavailable\n']
    assert start.call_args.args[0].startswith('503')
    assert all(c.args[0] == 'show' for c in service._command.call_args_list)


@pytest.mark.parametrize('quiet', [None, False, 1])
def test_temporary_cleanup_uncertainty_never_stops_services(composed, quiet):
    bindings, backend, service = composed
    bindings._quiescent.return_value = quiet
    with pytest.raises(DeliveryError):
        backend.cleanup(SHA)
    bindings._cleanup.assert_called_once_with(SHA)
    service._command.assert_not_called()
