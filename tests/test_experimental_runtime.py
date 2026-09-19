import json
from pathlib import Path

import pytest

from tools import experimental_runtime as runtime


@pytest.mark.parametrize('port', [8000, -1, 65536, True])
def test_forbidden_port_before_verification(tmp_path, port):
    with pytest.raises(ValueError, match='port'):
        runtime.launch(tmp_path, tmp_path, tmp_path, port)


def test_shared_runtime_root_rejected(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match='private'):
        runtime.launch(tmp_path, tmp_path, tmp_path)


def test_failed_candidate_preserves_previous(tmp_path, monkeypatch):
    pointer = tmp_path / 'current.json'
    pointer.write_text('previous')
    monkeypatch.setattr(runtime, 'verify', lambda *args: {'commit': 'a' * 40})
    class Child:
        pid = 123
        stopped = False
        def poll(self):
            return 1 if self.stopped else None
        def terminate(self):
            self.stopped = True
        def wait(self, timeout):
            return 1
    child = Child()
    def spawn(*args, **kwargs):
        assert set(kwargs['env']) == {'PATH', 'PYTHONDONTWRITEBYTECODE', 'TMPDIR'}
        return child
    monkeypatch.setattr(runtime.subprocess, 'Popen', spawn)
    monkeypatch.setattr(runtime, 'http_gate', lambda *args: (_ for _ in ()).throw(ValueError()))
    clock = iter([0, 21])
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: next(clock, 21))
    with pytest.raises(ValueError, match='timed out'):
        runtime.launch(tmp_path, tmp_path, tmp_path)
    assert child.stopped
    assert pointer.read_text() == 'previous'


def test_success_records_identity_without_stopping_child(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'verify', lambda *args: {'commit': 'a' * 40})
    class Child:
        pid = 123
        def poll(self):
            return None
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *a, **kw: Child())
    monkeypatch.setattr(runtime, 'http_gate', lambda *args: None)
    result = runtime.launch(tmp_path, tmp_path, tmp_path)
    assert json.loads((tmp_path / 'current.json').read_text()) == result
    assert result['commit'] == 'a' * 40
    assert not (Path(result['state']) / 'platform.db').exists()


@pytest.mark.parametrize('bad', ['commit', 'mode', 'status', 'html'])
def test_http_gate_rejects_bad_identity(monkeypatch, bad):
    class Response:
        status = 200
        def __init__(self, route):
            self.route = route
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            if self.route.endswith(('/health', '/api/today')):
                return json.dumps({'build_commit': 'b' * 40 if bad == 'commit' else 'a' * 40,
                    'mode': 'wrong' if bad == 'mode' else runtime.MODE,
                    'empirical_validation': 'NOT YET VALIDATED',
                    'status': 'wrong' if bad == 'status' else 'healthy'}).encode()
            return (runtime.MODE if bad == 'html' else runtime.MODE + 'a' * 40).encode()
    class Opener:
        def open(self, url, timeout):
            assert url.startswith('http://127.0.0.1:8123/')
            return Response(url)
    monkeypatch.setattr(runtime, 'build_opener', lambda *args: Opener())
    with pytest.raises(ValueError):
        runtime.http_gate(8123, 'a' * 40)


def test_bad_source_never_changes_pointer(tmp_path, monkeypatch):
    pointer = tmp_path / 'current.json'
    pointer.write_text('prior build')
    def reject(*args):
        raise ValueError('bad source')
    monkeypatch.setattr(runtime, 'verify', reject)
    with pytest.raises(ValueError, match='bad source'):
        runtime.launch(tmp_path, tmp_path, tmp_path)
    assert pointer.read_text() == 'prior build'


def runtime_record():
    return {'schema': 'experimental-runtime-v1', 'commit': 'a' * 40,
            'url': 'http://127.0.0.1:8123', 'mode': runtime.MODE,
            'http_gate': 'passed', 'pid': 123}


def test_current_status_requires_new_http_success(tmp_path, monkeypatch):
    pointer = tmp_path / 'current.json'
    pointer.write_text(json.dumps(runtime_record()))
    original = pointer.read_bytes()
    calls = []
    def gate(port, commit):
        calls.append((port, commit))
    monkeypatch.setattr(runtime, 'http_gate', gate)
    result = runtime.current_status(tmp_path)
    assert result['available'] is True
    assert result['build_commit'] == 'a' * 40
    assert result['scope'] == 'current network context'
    assert calls == [(8123, 'a' * 40)]
    def unavailable(*args):
        raise OSError('connection refused')
    monkeypatch.setattr(runtime, 'http_gate', unavailable)
    result = runtime.current_status(tmp_path)
    assert result['available'] is False
    assert result['reason'] == 'HTTP_GATE_UNAVAILABLE_OR_MISMATCHED'
    assert 'url' not in result and 'build_commit' not in result
    assert pointer.read_bytes() == original


@pytest.mark.parametrize('change', [
    {'url': 'http://127.0.0.1:8000'}, {'url': 'http://127.0.0.1:0'},
    {'url': 'http://127.0.0.1:65536'}, {'url': 'http://example.com:8123'},
    {'url': 'http://127.0.0.1:8123/health'}, {'url': 8123},
    {'commit': 'HEAD'}, {'mode': 'LIVE'}, {'http_gate': 'failed'},
    {'schema': 'unknown'},
])
def test_current_status_invalid_record_never_connects(tmp_path, monkeypatch, change):
    pointer = tmp_path / 'current.json'
    pointer.write_text(json.dumps({**runtime_record(), **change}))
    def forbidden(*args):
        pytest.fail('invalid runtime record must not cause HTTP access')
    monkeypatch.setattr(runtime, 'http_gate', forbidden)
    assert runtime.current_status(tmp_path)['reason'] == 'MISSING_OR_INVALID_RUNTIME_RECORD'


@pytest.mark.parametrize('content', ['null', '[]', '{broken'])
def test_current_status_bad_json_fails_closed(tmp_path, content):
    (tmp_path / 'current.json').write_text(content)
    assert runtime.current_status(tmp_path)['available'] is False


def test_current_status_missing_and_symlink_pointer(tmp_path):
    assert runtime.current_status(tmp_path)['available'] is False
    target = tmp_path / 'saved.json'
    target.write_text(json.dumps(runtime_record()))
    (tmp_path / 'current.json').symlink_to(target)
    assert runtime.current_status(tmp_path)['available'] is False


@pytest.mark.parametrize('port', [8000, 0, -1, 65536, True])
def test_http_gate_forbidden_port_never_connects(port, monkeypatch):
    def forbidden(*args):
        pytest.fail('forbidden port must not construct HTTP transport')
    monkeypatch.setattr(runtime, 'build_opener', forbidden)
    with pytest.raises(ValueError, match='port'):
        runtime.http_gate(port, 'a' * 40)
