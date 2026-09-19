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
