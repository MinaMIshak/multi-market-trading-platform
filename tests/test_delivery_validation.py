"""Validation contract with simulated host execution, never socket acceptance."""
import json
from pathlib import Path

import pytest

from tests.test_delivery_preparation import host
from tests.test_delivery_environment import prepared
from tools import delivery_validation as validation
from tools.delivery_runner import DeliveryError, SOCKET_TESTS


def xml(extra=''):
    cases = ''.join('<testcase classname="' + node.split('::')[0].removesuffix('.py').replace('/', '.')
                    + '" name="' + node.split('::')[1] + '"/>' for node in SOCKET_TESTS)
    return '<testsuites><testsuite>' + cases + extra + '</testsuite></testsuites>'


@pytest.fixture
def validator(host, tmp_path, monkeypatch):
    adapter, sha, _ = host
    adapter.prepare(sha)
    output, receipts = tmp_path / 'outputs', tmp_path / 'test-receipts'
    output.mkdir()
    receipts.mkdir()
    runtime = tmp_path / 'runtime-pin'
    runtime.write_text('immutable host runtime fixture')
    runner = validation.ReleaseTests(adapter, output, receipts,
                                    runtime_files={str(runtime): validation.environment.digest(runtime)})
    calls = []
    def run(argv, **kw):
        calls.append((argv, kw))
        Path(next(a.split('=', 1)[1] for a in argv if a.startswith('--junitxml='))).write_text(xml())
    monkeypatch.setattr(validation, 'run_owned', run)
    return runner, sha, calls, runtime


def test_exact_release_evidence_and_isolation(validator):
    runner, sha, calls, _ = validator
    result = runner.validate(sha)
    assert result['full_suite'] is True and result['runtime_rss'] is False
    assert result['socket_tests'] == dict.fromkeys(SOCKET_TESTS, True)
    assert result == json.loads((runner.receipts / (sha + '.json')).read_text())
    argv, kw = calls[0]
    assert argv[0] == str(runner.prepared.environments / sha / 'venv/bin/python')
    assert '-I' in argv and '-B' in argv and 'addopts=' in argv
    assert kw['cwd'] == runner.prepared.bundles / sha
    assert kw['env']['LIVE_MONEY'] == 'false'
    assert not {'PYTHONPATH', 'PYTEST_ADDOPTS'} & kw['env'].keys()
    with pytest.raises(DeliveryError, match='already exists'):
        runner.validate(sha)


@pytest.mark.parametrize('fault', ['failure', 'missing', 'skip', 'duplicate', 'malformed', 'empty', 'symlink', 'suite_error'])
def test_report_rejects_false_success(tmp_path, fault):
    path = tmp_path / 'report.xml'
    content = xml()
    if fault == 'failure': content = xml('<testcase><failure/></testcase>')
    if fault == 'missing': content = '<testsuites><testcase name="other"/></testsuites>'
    if fault == 'skip': content = content.replace('/>', '><skipped/></testcase>', 1)
    if fault == 'duplicate': content = content.replace('</testsuite>', content.split('<testsuite>')[1])
    if fault == 'malformed': content = '{'
    if fault == 'suite_error': content = content.replace('<testsuite>', '<testsuite errors="1">')
    if fault == 'empty': content = '<testsuites/>'
    path.write_text(content)
    if fault == 'symlink':
        other = tmp_path / 'other'
        path.rename(other)
        path.symlink_to(other)
    with pytest.raises((DeliveryError, ValueError)):
        validation.report(path)


@pytest.mark.parametrize('fault', ['command', 'runtime', 'source', 'report', 'interrupt', 'publish'])
def test_failure_retains_blocking_receipt(validator, monkeypatch, fault):
    runner, sha, calls, runtime = validator
    original = validation.run_owned
    def run(argv, **kw):
        original(argv, **kw)
        if fault == 'command': raise DeliveryError('injected failure')
        if fault == 'interrupt': raise KeyboardInterrupt()
        if fault == 'runtime': runtime.write_text('changed')
        if fault == 'source':
            manifest = runner.prepared.bundles / sha / 'RELEASE.json'
            # Any extra source file breaks the exact bundle inventory.
            root = manifest.parent
            root.chmod(0o755)
            (root / 'unexpected').write_text('mutation')
        if fault == 'report': (runner.outputs / sha / 'tests.xml').unlink()
    monkeypatch.setattr(validation, 'run_owned', run)
    original_write = validation.atomic_json
    def write(path, value):
        if fault == 'publish' and value['status'] == 'TESTED': raise OSError('disk failure')
        original_write(path, value)
    monkeypatch.setattr(validation, 'atomic_json', write)
    with pytest.raises((DeliveryError, KeyboardInterrupt, OSError)):
        runner.validate(sha)
    assert json.loads((runner.receipts / (sha + '.json')).read_text())['status'] == 'TESTING'


def test_runtime_change_rejected_before_execution(validator):
    runner, sha, calls, runtime = validator
    runtime.write_text('changed')
    with pytest.raises(DeliveryError, match='runtime mismatch'):
        runner.validate(sha)
    assert calls == [] and not list(runner.receipts.iterdir())
