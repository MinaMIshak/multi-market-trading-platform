"""Temporary-file invocation tests, not installed-host acceptance evidence."""
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tools.delivery_entrypoint import HostEntrypoint, protected_bytes
from tools.delivery_runner import DeliveryError
from tools.delivery_supervisor import DeliverySupervisor


@pytest.fixture
def setup(tmp_path):
    state = tmp_path / 'state'
    repository = tmp_path / 'repository'
    state.mkdir()
    repository.mkdir()
    command = {'argv': [str(Path(sys.executable).resolve()), '-I', 'installed.py'], 'timeout': 60}
    policy = dict(schema='delivery-host-v1', state=str(state), repository=str(repository),
                  writer=command.copy(), bridge=command.copy(), live_money=False)
    path = tmp_path / 'host.json'
    supervisor = DeliverySupervisor(state, SimpleNamespace(repository=repository), Mock(),
                                    launcher=Mock(), preflight=Mock())
    supervisor.startup = Mock()
    supervisor.run_cycle = Mock()
    supervisor.release = Mock(return_value={'status': 'FAILED'})
    factory = Mock(return_value=supervisor)
    def build():
        path.write_text(json.dumps(policy))
        path.chmod(0o600)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return HostEntrypoint(path, digest, factory, owner=os.getuid())
    with patch('tools.delivery_entrypoint._protected'):
        yield policy, path, supervisor, factory, build


def test_cycle_uses_only_pinned_commands_and_recovers_first(setup):
    policy, _, supervisor, _, build = setup
    entry = build()
    calls = Mock()
    calls.attach_mock(supervisor.startup, 'startup')
    calls.attach_mock(supervisor.run_cycle, 'cycle')
    entry.invoke('cycle')
    assert [c[0] for c in calls.mock_calls] == ['startup', 'cycle']
    supervisor.run_cycle.assert_called_once_with(tuple(policy['writer']['argv']),
        tuple(policy['bridge']['argv']), cwd=Path(policy['repository']),
        writer_timeout=60, bridge_timeout=60)


def test_release_preserves_runner_result(setup):
    _, _, supervisor, _, build = setup
    request = {'commit': 'a' * 40}
    assert build().invoke('release', request) == {'status': 'FAILED'}
    supervisor.release.assert_called_once_with(request)


@pytest.mark.parametrize('operation', ['startup', 'cycle', 'release'])
def test_restart_failure_blocks_admission(setup, operation):
    _, _, supervisor, _, build = setup
    supervisor.startup.side_effect = DeliveryError('unresolved writer')
    with pytest.raises(DeliveryError, match='unresolved'):
        build().invoke(operation, {} if operation == 'release' else None)
    supervisor.run_cycle.assert_not_called()
    supervisor.release.assert_not_called()


@pytest.mark.parametrize('field,value', [('live_money', True), ('live_money', 0),
    ('schema', 'unknown'), ('extra', 'override'), ('state', '/tmp/../state')])
def test_invalid_policy_never_constructs_host(setup, field, value):
    policy, _, _, factory, build = setup
    policy[field] = value
    with pytest.raises(DeliveryError):
        build()
    factory.assert_not_called()


@pytest.mark.parametrize('timeout', [0, -1, True, float('inf'), float('nan'), 86401, '60'])
def test_invalid_deadline(setup, timeout):
    policy, _, _, factory, build = setup
    policy['writer']['timeout'] = timeout
    with pytest.raises(DeliveryError):
        build()
    factory.assert_not_called()


@pytest.mark.parametrize('operation,payload', [('shell', None), ('cycle', {}),
    ('startup', {}), ('release', None), ('release', 'request.json')])
def test_invocation_has_no_command_or_path_overrides(setup, operation, payload):
    _, _, supervisor, _, build = setup
    with pytest.raises(DeliveryError):
        build().invoke(operation, payload)
    supervisor.startup.assert_not_called()


@pytest.mark.parametrize('drift', ['config', 'state', 'repository', 'identity'])
def test_drift_blocks_before_recovery(setup, drift):
    _, path, supervisor, _, build = setup
    entry = build()
    if drift == 'config':
        path.write_text('{}')
    elif drift == 'state':
        supervisor.state = path.parent
    elif drift == 'repository':
        supervisor.source.repository = path.parent
    else:
        entry.pid = -1
    with pytest.raises(DeliveryError):
        entry.invoke('cycle')
    supervisor.startup.assert_not_called()


def test_pin_rejects_replaced_config_before_factory(setup):
    _, path, _, factory, build = setup
    build()
    factory.reset_mock()
    with pytest.raises(DeliveryError, match='pin mismatch'):
        HostEntrypoint(path, '0' * 64, factory, owner=os.getuid())
    factory.assert_not_called()


@pytest.mark.parametrize('field', ['preflight', 'launcher'])
def test_unbound_composition_rejected(setup, field):
    _, _, supervisor, _, build = setup
    setattr(supervisor, field, None)
    with pytest.raises(DeliveryError, match='composition'):
        build()


def test_duplicate_configuration_keys_rejected(setup):
    _, path, _, factory, build = setup
    build()
    path.write_text('{"schema": 1, "schema": 2}')
    factory.reset_mock()
    with pytest.raises(DeliveryError, match='duplicate'):
        HostEntrypoint(path, hashlib.sha256(path.read_bytes()).hexdigest(), factory,
                       owner=os.getuid())
    factory.assert_not_called()


@pytest.mark.parametrize('kind', ['symlink', 'writable', 'oversized'])
def test_unsafe_file_rejected(tmp_path, kind):
    path = tmp_path / 'policy'
    path.write_text('{}')
    path.chmod(0o600)
    if kind == 'symlink':
        link = tmp_path / 'link'
        link.symlink_to(path)
        path = link
    elif kind == 'writable':
        path.chmod(0o666)
    else:
        path.write_bytes(b' ' * (1024 * 1024 + 1))
    with patch('tools.delivery_entrypoint._protected'):
        with pytest.raises((DeliveryError, OSError)):
            protected_bytes(path, os.getuid())


@pytest.mark.parametrize('field', ['source', 'host', 'launcher', 'preflight', 'reconcile_writer'])
def test_composition_replacement_rejected(setup, field):
    _, _, supervisor, _, build = setup
    entry = build()
    setattr(supervisor, field, Mock())
    with pytest.raises(DeliveryError, match='composition changed'):
        entry.invoke('startup')
    supervisor.startup.assert_not_called()


def test_current_identity_required_before_config_read(setup):
    _, path, _, factory, _ = setup
    with pytest.raises(DeliveryError, match='identity'):
        HostEntrypoint(path, '0' * 64, factory, owner=os.getuid() + 1)
    factory.assert_not_called()
