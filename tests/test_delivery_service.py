"""No host systemd or sockets accessed; service observations are injected."""
import hashlib
from unittest.mock import Mock

import pytest

from tools.delivery_runner import DeliveryError
from tools.delivery_service import ReleaseService

SHA = 'a' * 40


@pytest.fixture
def service(tmp_path):
    fragment = tmp_path / f'egx-release-{SHA}.service'
    fragment.write_text('host-reviewed unit fixture')
    props = dict(LoadState='loaded', NeedDaemonReload='no', DropInPaths='',
                 FragmentPath=str(fragment), KillMode='control-group', SendSIGKILL='yes',
                 NoNewPrivileges='yes', ProtectSystem='strict', ProtectControlGroups='yes',
                 Delegate='no', Type='exec', Restart='on-failure', ExecStart='pinned command',
                 WorkingDirectory='/release/source', User='egx',
                 Environment=f'LIVE_MONEY=false EGX_BUILD_REVISION={SHA}', ReadWritePaths='/isolated/state')
    policy = dict(fragment=str(fragment), sha256=hashlib.sha256(fragment.read_bytes()).hexdigest(),
                  properties=props)
    probe = Mock(return_value=dict(commit=SHA, live_money=False, http=True, api=True,
                                   runtime_compatible=True, sig_v1=True))
    s = ReleaseService('/missing/systemctl', '0' * 64, {SHA: policy},
                       Mock(return_value=True), Mock(return_value=True), probe)
    s.observed = {**props, 'ActiveState': 'active', 'SubState': 'running',
                  'MainPID': '1234', 'ControlGroup': '/system.slice/fixture.service'}
    s._command = Mock(side_effect=lambda *args: '' if args[0] == 'start' else
                      ''.join(f'{k}={v}\n' for k, v in s.observed.items()))
    return s


def test_staging_and_restart_keep_previous_services(service):
    for _ in range(2):
        assert service.contained(SHA) is True
        assert service.stage(SHA)['commit'] == SHA
    commands = [c.args for c in service._command.call_args_list]
    assert all(c[0] in ('show', 'start') for c in commands)
    assert [c for c in commands if c[0] == 'start'] == [('start', f'egx-release-{SHA}.service')] * 2


@pytest.mark.parametrize('key,value', [('KillMode', 'process'), ('DropInPaths', '/override'),
    ('NeedDaemonReload', 'yes'), ('ExecStart', 'other'), ('Environment', 'LIVE_MONEY=true'),
    ('ProtectSystem', 'no'), ('User', 'root')])
def test_loaded_policy_drift_prevents_start(service, key, value):
    service.observed[key] = value
    with pytest.raises(DeliveryError):
        service.stage(SHA)
    assert all(c.args[0] != 'start' for c in service._command.call_args_list)


@pytest.mark.parametrize('key,value', [('ActiveState', 'failed'), ('SubState', 'exited'),
    ('MainPID', '0'), ('MainPID', 'nan'), ('ControlGroup', ''), ('ControlGroup', '/')])
def test_running_state_required_before_probe(service, key, value):
    service.observed[key] = value
    with pytest.raises(DeliveryError):
        service.stage(SHA)
    service.probe.assert_not_called()


@pytest.mark.parametrize('gate', ['live_money', 'http', 'api', 'runtime_compatible', 'commit'])
def test_api_gate_failure(service, gate):
    service.probe.return_value[gate] = True if gate == 'live_money' else False
    with pytest.raises(DeliveryError):
        service.stage(SHA)


def test_previous_release_without_sig_can_be_restaged(service):
    service.probe.return_value.pop('sig_v1')
    assert service.stage(SHA)['commit'] == SHA


@pytest.mark.parametrize('value', [False, None, 1])
def test_isolation_requires_strict_truth(service, value):
    service.verify_isolation.return_value = value
    with pytest.raises(DeliveryError):
        service.stage(SHA)


def test_probe_time_drift_rechecked(service):
    def probe(sha):
        service.observed['KillMode'] = 'process'
        return dict(commit=sha, live_money=False, http=True, api=True, runtime_compatible=True)
    service.probe.side_effect = probe
    with pytest.raises(DeliveryError):
        service.stage(SHA)


def test_fragment_tampering_rejected(service):
    from pathlib import Path
    Path(service.policies[SHA]['fragment']).write_text('changed')
    with pytest.raises(DeliveryError):
        service.stage(SHA)
    service._command.assert_not_called()


@pytest.mark.parametrize('output', ['', 'LoadState=loaded\nLoadState=loaded\n', 'bad line\n'])
def test_incomplete_duplicate_malformed_observation(service, output):
    service._command.side_effect = None
    service._command.return_value = output
    with pytest.raises(DeliveryError):
        service.stage(SHA)


def test_unapproved_sha_rejected(service):
    with pytest.raises(DeliveryError):
        service.stage('b' * 40)
    service._command.assert_not_called()


def test_unverified_release_rejected(service):
    service.verify_release.return_value = None
    with pytest.raises(DeliveryError):
        service.stage(SHA)
    service._command.assert_not_called()


def test_manager_pin_and_command_contract(service, tmp_path, monkeypatch):
    executable = tmp_path / 'systemctl'
    executable.write_bytes(b'fixture executable')
    service.systemctl = executable
    service.digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    run = Mock(return_value=Mock(returncode=0, stdout='observed'))
    monkeypatch.setattr('tools.delivery_service.subprocess.run', run)
    assert ReleaseService._command(service, 'show', 'unit') == 'observed'
    assert run.call_args.args[0] == [str(executable), 'show', 'unit']
    assert run.call_args.kwargs['timeout'] == 30
    executable.write_bytes(b'changed')
    with pytest.raises(DeliveryError):
        ReleaseService._command(service, 'start', 'unit')
    assert run.call_count == 1


@pytest.mark.parametrize('environment', ['LIVE_MONEY=true', 'LIVE_MONEY=false',
    f'LIVE_MONEY=false LIVE_MONEY=true EGX_BUILD_REVISION={SHA}'])
def test_unsafe_policy_environment_rejected(service, environment):
    service.policies[SHA]['properties']['Environment'] = environment
    service.observed['Environment'] = environment
    with pytest.raises(DeliveryError):
        service.stage(SHA)
    service._command.assert_not_called()


def test_read_only_observation_never_activates(service):
    assert service.observe(SHA)['commit'] == SHA
    assert all(c.args[0] == 'show' for c in service._command.call_args_list)
    service.observed['ActiveState'] = 'inactive'
    service.probe.reset_mock()
    with pytest.raises(DeliveryError):
        service.observe(SHA)
    service.probe.assert_not_called()
    assert all(c.args[0] == 'show' for c in service._command.call_args_list)
