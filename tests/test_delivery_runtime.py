"""Simulated host metrics only; no nightly or socket acceptance claims."""
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from tests.test_delivery_environment import prepared
from tests.test_delivery_preparation import host
from tests.test_delivery_validation import validator
from tools import delivery_runtime as runtime
from tools.delivery_runner import DeliveryError


@pytest.fixture
def measurement(validator, tmp_path, monkeypatch):
    tests, sha, _, pin = validator
    tests.validate(sha)
    outputs, receipts = tmp_path / 'runtime-outputs', tmp_path / 'runtime-receipts'
    outputs.mkdir()
    receipts.mkdir()
    policy = {'script': 'app/ui/system.py', 'args': [],
              'inputs': {str(pin): runtime.environment.digest(pin)},
              'max_seconds': 10, 'max_rss_kib': 10000}
    def launch(key, argv, **kwargs):
        assert key == 'runtime'
        return runtime.run_owned(argv, **kwargs)
    runner = runtime.ReleaseRuntime(tests, outputs, receipts, timer=pin,
                                   timer_sha256=runtime.environment.digest(pin),
                                   workloads={'EGX': policy, 'US': policy}, launcher=SimpleNamespace(run=launch))
    calls = []
    def run(argv, **kw):
        calls.append((argv, kw))
        Path(argv[argv.index('-o') + 1]).write_text('1.25 1000 0\n')
    monkeypatch.setattr(runtime, 'run_owned', run, raising=False)
    return runner, sha, calls, pin


def test_measurement_binds_both_markets_and_reopens(measurement):
    runner, sha, calls, _ = measurement
    result = runner.validate(sha)
    assert result == runner.verify(sha)
    assert result['runtime_rss'] is True and result['candidate_validation'] == 'NOT_RUN'
    assert set(result['runtime_evidence']['markets']) == {'EGX', 'US'}
    for argv, kw in calls:
        assert argv[5] == str(runner.tests.prepared.environments / sha / 'venv/bin/python')
        assert kw['env']['LIVE_MONEY'] == 'false'
        assert kw['cwd'].parent == runner.outputs / sha
        assert str(runner.tests.prepared.bundles / sha / 'source') in argv
    with pytest.raises(DeliveryError, match='already exists'):
        runner.validate(sha)


@pytest.mark.parametrize('fault', ['command', 'interrupt', 'input', 'report', 'rss', 'wall',
                                  'nan', 'exit', 'missing', 'symlink', 'publish'])
def test_failure_retains_measurement_latch(measurement, monkeypatch, fault):
    runner, sha, calls, pin = measurement
    original = runtime.run_owned
    def run(argv, **kw):
        original(argv, **kw)
        path = Path(argv[argv.index('-o') + 1])
        if fault == 'command': raise DeliveryError('command failed')
        if fault == 'interrupt': raise KeyboardInterrupt()
        if fault == 'input': pin.write_text('mutation')
        if fault == 'missing': path.unlink()
        if fault == 'symlink':
            path.rename(path.with_suffix('.other'))
            path.symlink_to(path.with_suffix('.other'))
        if fault in ('report', 'rss', 'wall', 'nan', 'exit'):
            path.write_text({'report': 'bad', 'rss': '1 10001 0', 'wall': '11 1 0',
                             'nan': 'nan 1 0', 'exit': '1 1 2'}[fault])
    monkeypatch.setattr(runtime, 'run_owned', run, raising=False)
    write = runtime.atomic_json
    def publish(path, value):
        if fault == 'publish' and value['status'] == 'MEASURED': raise OSError('disk failure')
        write(path, value)
    monkeypatch.setattr(runtime, 'atomic_json', publish)
    with pytest.raises((DeliveryError, KeyboardInterrupt, ValueError, OSError)):
        runner.validate(sha)
    assert json.loads((runner.receipts / (sha + '.json')).read_text())['status'] == 'MEASURING'
    with pytest.raises((DeliveryError, ValueError)):
        runner.verify(sha)


@pytest.mark.parametrize('fault', ['tests', 'policy', 'metrics', 'live', 'status'])
def test_restart_rejects_changed_evidence(measurement, fault):
    runner, sha, _, _ = measurement
    runner.validate(sha)
    path = runner.receipts / (sha + '.json')
    if fault == 'tests': (runner.tests.receipts / (sha + '.json')).write_text('{}')
    elif fault == 'policy': runner.workloads['US']['max_seconds'] = 20
    elif fault == 'metrics': (runner.outputs / sha / 'US/time.txt').write_text('1 900 0')
    else:
        value = json.loads(path.read_text())
        value['live_money' if fault == 'live' else 'status'] = 0 if fault == 'live' else 'MEASURING'
        path.write_text(json.dumps(value))
    with pytest.raises((DeliveryError, ValueError)):
        runner.verify(sha)


@pytest.mark.parametrize('fault', ['missing_market', 'unbounded', 'nan', 'traversal', 'inputs', 'overlap'])
def test_unsafe_policy_rejected(measurement, fault):
    runner, _, calls, _ = measurement
    policy = runner.workloads
    outputs = runner.outputs
    if fault == 'missing_market': del policy['US']
    elif fault == 'unbounded': policy['US']['max_seconds'] = float('inf')
    elif fault == 'nan': policy['US']['max_rss_kib'] = float('nan')
    elif fault == 'traversal': policy['US']['script'] = '../outside.py'
    elif fault == 'inputs': policy['US']['inputs'] = {}
    else: outputs = runner.tests.outputs
    with pytest.raises(DeliveryError):
        runtime.ReleaseRuntime(runner.tests, outputs, runner.receipts,
                               timer=runner.timer, timer_sha256=runner.timer_sha256,
                               workloads=policy)
    assert calls == []


def test_incomplete_tests_block_runtime_before_marker(measurement):
    runner, sha, calls, _ = measurement
    path = runner.tests.receipts / (sha + '.json')
    value = json.loads(path.read_text())
    value['status'] = 'TESTING'
    path.write_text(json.dumps(value))
    with pytest.raises(DeliveryError):
        runner.validate(sha)
    assert calls == [] and not list(runner.receipts.iterdir())


@pytest.mark.parametrize('uncertain', [False, True])
def test_launcher_failure_cannot_publish_measurement(measurement, uncertain):
    from tools.delivery_process import CleanupError
    runner, sha, calls, _ = measurement
    def reject(*args, **kwargs):
        raise CleanupError('cleanup uncertain')
    runner.launcher = SimpleNamespace(run=reject) if uncertain else None
    with pytest.raises(CleanupError if uncertain else DeliveryError):
        runner.validate(sha)
    assert not calls
    receipt = runner.receipts / (sha + '.json')
    if uncertain:
        assert json.loads(receipt.read_text())['status'] == 'MEASURING'
    else:
        assert not receipt.exists()
