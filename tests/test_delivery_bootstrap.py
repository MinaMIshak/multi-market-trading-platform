"""Offline manifest tests; no installed host or containment evidence."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import FunctionType, ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tools import delivery_bootstrap
from tools.delivery_bootstrap import InstalledBootstrap, _gate_sources
from tools.delivery_runner import DeliveryError


def factory():
    raise AssertionError('real factory must not run in manifest tests')


@pytest.fixture
def setup(tmp_path):
    config = tmp_path / 'host.json'
    dependency = tmp_path / 'dependency.py'
    manifest = tmp_path / 'bootstrap.json'
    config.write_text('{}')
    dependency.write_text('# installed dependency\n')
    manifest.write_text('')
    for path in (config, dependency, manifest):
        path.chmod(0o600)
    source = tmp_path / 'installed_factory.py'
    source.write_text('def factory():\n    raise AssertionError("stub entrypoint only")\n')
    source.chmod(0o600)
    module = ModuleType('installed_test_factory')
    module.__file__ = str(source)
    exec(compile(source.read_text(), str(source), 'exec'), module.__dict__)
    pin = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    policy = dict(schema='delivery-bootstrap-v1', configuration=str(config),
                  files={str(p): pin(p) for p in (config, dependency, source)}, live_money=False)
    def build(candidate=None):
        manifest.write_text(json.dumps(policy))
        return InstalledBootstrap(manifest, pin(manifest), candidate or module.factory, owner=os.getuid())
    # The real tools modules are not pinned here; loaded-module coverage has its own tests.
    with patch.dict(sys.modules, {module.__name__: module}), \
            patch('tools.delivery_entrypoint._protected'), \
            patch('tools.delivery_bootstrap._gate_sources', return_value=set()), \
            patch('tools.delivery_bootstrap.HostEntrypoint') as entry:
        yield policy, manifest, dependency, entry, build


def test_validated_factory_and_dispatch(setup):
    policy, _, _, entry, build = setup
    bootstrap = build()
    entry.assert_called_once_with(Path(policy['configuration']),
        policy['files'][policy['configuration']],
        sys.modules['installed_test_factory'].factory, owner=os.getuid())
    request = {'commit': 'a' * 40}
    assert bootstrap.invoke('release', request) is entry.return_value.invoke.return_value
    entry.return_value.invoke.assert_called_once_with('release', request)


@pytest.mark.parametrize('change', ['dependency', 'manifest', 'configuration', 'identity'])
def test_drift_prevents_dispatch(setup, change):
    policy, manifest, dependency, entry, build = setup
    bootstrap = build()
    if change == 'identity':
        bootstrap.pid = -1
    else:
        path = {'dependency': dependency, 'manifest': manifest,
                'configuration': Path(policy['configuration'])}[change]
        path.write_text('changed')
    with pytest.raises(DeliveryError):
        bootstrap.invoke('cycle')
    entry.return_value.invoke.assert_not_called()


@pytest.mark.parametrize('change', ['factory', 'configuration', 'pin', 'empty', 'live', 'extra',
                                    'noncanonical'])
def test_bad_policy_rejected_before_factory(setup, change):
    policy, _, dependency, entry, build = setup
    if change == 'factory':
        del policy['files'][sys.modules['installed_test_factory'].__file__]
    elif change == 'configuration':
        del policy['files'][policy['configuration']]
    elif change == 'pin':
        policy['files'][str(dependency)] = '0' * 64
    elif change == 'empty':
        policy['files'] = {}
    elif change == 'live':
        policy['live_money'] = True
    elif change == 'noncanonical':
        policy['files']['/tmp/../dependency.py'] = '0' * 64
    else:
        policy['factory_import'] = 'untrusted.module'
    with pytest.raises(DeliveryError):
        build()
    entry.assert_not_called()


def test_construction_cannot_change_dependencies(setup):
    _, _, dependency, entry, build = setup
    entry.side_effect = lambda *a, **k: dependency.write_text('mutated')
    with pytest.raises(DeliveryError, match='file pin mismatch'):
        build()


@pytest.mark.parametrize('kind', ['symlink', 'writable', 'missing'])
def test_unsafe_dependency_blocks_dispatch(setup, kind):
    _, _, dependency, entry, build = setup
    bootstrap = build()
    if kind == 'symlink':
        target = dependency.with_suffix('.target')
        dependency.rename(target)
        dependency.symlink_to(target)
    elif kind == 'writable':
        dependency.chmod(0o666)
    else:
        dependency.unlink()
    with pytest.raises((DeliveryError, OSError)):
        bootstrap.invoke('startup')
    entry.return_value.invoke.assert_not_called()


@pytest.mark.parametrize('raw', [b'{', b'\xff', b'[]', b'{"schema": 1, "schema": 2}',
                                 b'[' * 100000], ids=['open', 'bytes', 'list', 'dup', 'deep'])
def test_malformed_manifest_is_delivery_error(setup, raw):
    _, manifest, _, entry, _ = setup
    manifest.write_bytes(raw)
    with pytest.raises(DeliveryError):
        InstalledBootstrap(manifest, hashlib.sha256(raw).hexdigest(), factory, owner=os.getuid())
    entry.assert_not_called()


def test_manifest_digest_and_identity_required(setup):
    _, manifest, _, entry, build = setup
    build()
    entry.reset_mock()
    with pytest.raises(DeliveryError, match='pin mismatch'):
        InstalledBootstrap(manifest, '0' * 64, factory, owner=os.getuid())
    with pytest.raises(DeliveryError, match='identity'):
        InstalledBootstrap(manifest, '0' * 64, factory, owner=os.getuid() + 1)
    entry.assert_not_called()


@pytest.mark.parametrize('kind', ['callable', 'nested', 'detached'])
def test_factory_must_be_pinned_module_function(setup, kind):
    _, _, _, entry, build = setup
    def nested():
        raise AssertionError('must not run')
    candidate = {'callable': Mock(), 'nested': nested,
                 'detached': FunctionType(factory.__code__, globals(), 'factory')}[kind]
    with pytest.raises(DeliveryError, match='factory'):
        build(candidate)
    entry.assert_not_called()


def test_factory_source_path_cannot_be_spoofed(setup):
    _, _, dependency, entry, build = setup
    code = factory.__code__.replace(co_filename=str(dependency))
    spoofed = FunctionType(code, globals(), 'factory')
    with patch.object(sys.modules[__name__], 'factory', spoofed):
        with pytest.raises(DeliveryError, match='source mismatch'):
            build(spoofed)
    entry.assert_not_called()


def test_unpinned_loaded_module_blocks_factory(setup):
    _, _, _, entry, build = setup
    with patch('tools.delivery_bootstrap._gate_sources',
               return_value={Path('/installed/tools/delivery_unpinned.py')}):
        with pytest.raises(DeliveryError, match='not pinned'):
            build()
    entry.assert_not_called()


def test_module_loaded_during_construction_is_rejected(setup):
    _, _, _, entry, build = setup
    with patch('tools.delivery_bootstrap._gate_sources',
               side_effect=[set(), {Path('/installed/tools/delivery_late.py')}]):
        with pytest.raises(DeliveryError, match='not pinned'):
            build()
    entry.assert_called_once()


def test_module_loaded_after_construction_blocks_dispatch(setup):
    _, _, _, entry, build = setup
    bootstrap = build()
    with patch('tools.delivery_bootstrap._gate_sources',
               return_value={Path('/installed/tools/delivery_late.py')}):
        with pytest.raises(DeliveryError, match='not pinned'):
            bootstrap.invoke('startup')
    entry.return_value.invoke.assert_not_called()


def test_gate_sources_cover_loaded_tools_modules():
    late = SimpleNamespace(__file__='/installed/tools/preview_late.py')
    with patch.dict(sys.modules, {'tools.preview_late': late}):
        sources = _gate_sources()
    assert Path('/installed/tools/preview_late.py') in sources
    assert Path(delivery_bootstrap.__file__) in sources


def test_gate_sources_fail_closed_without_module_file():
    with patch.dict(sys.modules, {'tools.delivery_unknown': SimpleNamespace()}):
        with pytest.raises(DeliveryError):
            _gate_sources()


def test_factory_module_rebinding_blocks_dispatch(setup):
    _, _, _, entry, build = setup
    bootstrap = build()
    with patch.object(sys.modules['installed_test_factory'], 'factory', Mock()):
        with pytest.raises(DeliveryError, match='factory'):
            bootstrap.invoke('cycle')
    entry.return_value.invoke.assert_not_called()


def test_entrypoint_replacement_blocks_dispatch(setup):
    _, _, _, entry, build = setup
    bootstrap = build()
    replacement = Mock()
    bootstrap.entrypoint = replacement
    with pytest.raises(DeliveryError, match='entrypoint changed'):
        bootstrap.invoke('release', {})
    replacement.invoke.assert_not_called()
    entry.return_value.invoke.assert_not_called()


@pytest.mark.parametrize('change', ['schema', 'files_list', 'too_many', 'uppercase_pin',
                                    'relative_config', 'oversize'])
def test_manifest_contract_bounds(setup, change):
    policy, _, dependency, entry, build = setup
    if change == 'schema':
        policy['schema'] = 'delivery-bootstrap-v2'
    elif change == 'files_list':
        policy['files'] = []
    elif change == 'too_many':
        policy['files'] = {f'/installed/{i}': '0' * 64 for i in range(1025)}
    elif change == 'uppercase_pin':
        policy['files'][str(dependency)] = 'A' * 64
    elif change == 'relative_config':
        policy['configuration'] = 'host.json'
    else:
        dependency.write_bytes(b'x' * (1024 * 1024 + 1))
        policy['files'][str(dependency)] = hashlib.sha256(dependency.read_bytes()).hexdigest()
    with pytest.raises(DeliveryError):
        build()
    entry.assert_not_called()


def test_bootstrap_integrates_with_entrypoint_recovery_before_cycle(setup):
    from tools.delivery_entrypoint import HostEntrypoint
    from tools.delivery_supervisor import DeliverySupervisor

    policy, _, dependency, entry, build = setup
    root = dependency.parent
    state, repository = root / 'state', root / 'repository'
    state.mkdir()
    repository.mkdir()
    executable = root / 'fixed-command'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o700)
    command = {'argv': [str(executable)], 'timeout': 30}
    configuration = Path(policy['configuration'])
    configuration.write_text(json.dumps(dict(schema='delivery-host-v1', state=str(state),
        repository=str(repository), writer=command, bridge=command, live_money=False)))
    policy['files'][str(configuration)] = hashlib.sha256(configuration.read_bytes()).hexdigest()
    supervisor = DeliverySupervisor(state, SimpleNamespace(repository=repository), Mock(),
                                    launcher=Mock(), preflight=Mock())
    calls = Mock()
    supervisor.startup = calls.startup
    supervisor.run_cycle = calls.cycle
    module = sys.modules['installed_test_factory']
    source = Path(module.__file__)
    source.write_text('def factory():\n    return supervisor\n')
    module.supervisor = supervisor
    exec(compile(source.read_text(), str(source), 'exec'), module.__dict__)
    policy['files'][str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
    entry.side_effect = HostEntrypoint
    bootstrap = build()
    bootstrap.invoke('cycle')
    assert [call[0] for call in calls.mock_calls] == ['startup', 'cycle']
    calls.cycle.assert_called_once_with((str(executable),), (str(executable),),
        cwd=repository, writer_timeout=30, bridge_timeout=30)
    calls.reset_mock()
    supervisor.startup.side_effect = DeliveryError('recovery incomplete')
    with pytest.raises(DeliveryError, match='recovery incomplete'):
        bootstrap.invoke('cycle')
    calls.cycle.assert_not_called()


def test_completion_dispatch_checks_pins_and_entrypoint(setup):
    _, _, dependency, entry, build = setup
    bootstrap = build()
    assert bootstrap.completion('/host/event.json', 'a' * 64) is entry.return_value.completion.return_value
    entry.return_value.completion.assert_called_once_with('/host/event.json', 'a' * 64)
    dependency.write_text('changed')
    with pytest.raises(DeliveryError):
        bootstrap.completion('/host/event.json', 'a' * 64)
    assert entry.return_value.completion.call_count == 1


def test_report_completion_checks_pins_before_retention(setup):
    _, _, dependency, entry, build = setup
    bootstrap = build()
    bootstrap.report_completion(event_id='host-event')
    entry.return_value.report_completion.assert_called_once_with(event_id='host-event')
    dependency.write_text('drift')
    with pytest.raises(DeliveryError):
        bootstrap.report_completion(event_id='other-event')
    assert entry.return_value.report_completion.call_count == 1
