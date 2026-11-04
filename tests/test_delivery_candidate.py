"""Simulated spare-port lifecycle; no sandbox socket retries or runtime claims."""
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from tests.test_delivery_environment import prepared
from tests.test_delivery_preparation import host
from tests.test_delivery_validation import validator
from tests.test_delivery_runtime import measurement
from tools import delivery_candidate as candidate
from tools.delivery_runner import DeliveryError


@pytest.fixture
def gate(measurement, tmp_path, monkeypatch):
    runtime, sha, _, pin = measurement
    runtime.validate(sha)
    outputs, receipts = tmp_path / 'candidate-outputs', tmp_path / 'candidate-receipts'
    outputs.mkdir()
    receipts.mkdir()
    routes = {name: {'route': '/' + name, 'checks': [{'path': ['status'], 'value': 'ok'}]}
              for name in ('http', 'api', 'runtime_compatible', 'sig_v1')}
    runner = candidate.ReleaseCandidate(runtime, outputs, receipts, factory=pin,
        factory_sha256=candidate.environment.digest(pin),
        inputs={'snapshot.json': {'path': str(pin), 'sha256': candidate.environment.digest(pin)}},
        routes=routes)
    events = []
    class Listener:
        def __enter__(self): return self
        def __exit__(self, *args): events.append('socket_closed')
        def bind(self, address):
            assert address == ('127.0.0.1', 0)
            events.append('bound')
        def getsockname(self): return ('127.0.0.1', 43210)
        def listen(self, backlog): pass
        def fileno(self): return 42
    monkeypatch.setattr(candidate, 'socket', SimpleNamespace(socket=lambda *a: Listener(), AF_INET=2, SOCK_STREAM=1))
    @contextmanager
    def owned(argv, **kw):
        events.append(('spawn', argv, kw))
        try:
            yield SimpleNamespace(poll=lambda: None)
        finally:
            events.append('cleanup')
    monkeypatch.setattr(candidate, 'owned_process', owned)
    def probe(port, commit, routes, **kwargs):
        assert port == 43210 and commit == sha
        events.append('probe')
        return {key: {'route': value['route'], 'body_sha256': 'a' * 64}
                for key, value in routes.items()}
    monkeypatch.setattr(candidate, 'probe', probe)
    return runner, sha, events, pin


def test_candidate_bound_evidence_and_cleanup(gate):
    runner, sha, events, _ = gate
    result = runner.validate(sha)
    assert result == runner.verify(sha)
    assert result['status'] == 'ACCEPTED' and result['live_money'] is False
    assert events[-2:] == ['cleanup', 'socket_closed']
    _, argv, kw = events[1]
    assert argv[0] == str(runner.runtime.tests.prepared.environments / sha / 'venv/bin/python')
    assert kw['pass_fds'] == (42,) and kw['env']['LIVE_MONEY'] == 'false'
    assert (runner.outputs / sha / 'state/snapshot.json').is_file()
    with pytest.raises(DeliveryError, match='already exists'):
        runner.validate(sha)


@pytest.mark.parametrize('fault', ['http', 'interrupt', 'dependencies', 'cleanup', 'publish', 'exit'])
def test_failure_preserves_incomplete_receipt(gate, monkeypatch, fault):
    runner, sha, events, pin = gate
    original = candidate.probe
    def probe(*args, **kwargs):
        if fault == 'http': raise DeliveryError('bad identity')
        if fault == 'interrupt': raise KeyboardInterrupt()
        if fault == 'dependencies': pin.write_text('mutated')
        return original(*args, **kwargs)
    monkeypatch.setattr(candidate, 'probe', probe)
    owned = candidate.owned_process
    @contextmanager
    def process(*args, **kw):
        with owned(*args, **kw) as child:
            yield SimpleNamespace(poll=lambda: 1) if fault == 'exit' else child
        if fault == 'cleanup': raise DeliveryError('cleanup unconfirmed')
    monkeypatch.setattr(candidate, 'owned_process', process)
    write = candidate.atomic_json
    def publish(path, value):
        if fault == 'publish' and value['status'] == 'ACCEPTED': raise OSError('disk failure')
        write(path, value)
    monkeypatch.setattr(candidate, 'atomic_json', publish)
    with pytest.raises((DeliveryError, KeyboardInterrupt, OSError)):
        runner.validate(sha)
    assert json.loads((runner.receipts / (sha + '.json')).read_text())['status'] == 'CHECKING'
    assert events[-2:] == ['cleanup', 'socket_closed']
    with pytest.raises(DeliveryError): runner.verify(sha)


@pytest.mark.parametrize('fault', ['runtime', 'policy', 'live', 'port', 'status', 'observations'])
def test_restart_rejects_unbound_evidence(gate, fault):
    runner, sha, _, _ = gate
    result = runner.validate(sha)
    if fault == 'runtime': (runner.runtime.receipts / (sha + '.json')).write_text('{}')
    elif fault == 'policy': runner.routes['api']['checks'][0]['value'] = 'changed'
    else:
        result[{'live': 'live_money', 'port': 'port', 'status': 'status', 'observations': 'observations'}[fault]] = {
            'live': 0, 'port': 8001, 'status': 'CHECKING', 'observations': {}}[fault]
        (runner.receipts / (sha + '.json')).write_text(json.dumps(result))
    with pytest.raises(DeliveryError): runner.verify(sha)


@pytest.mark.parametrize('fault', ['gates', 'empty', 'traversal', 'overlap', 'deadline'])
def test_unsafe_candidate_policy(gate, fault):
    runner, _, events, _ = gate
    if fault == 'gates': del runner.routes['sig_v1']
    if fault == 'empty': runner.routes['sig_v1']['checks'] = []
    if fault == 'traversal': runner.inputs['../outside'] = runner.inputs.pop('snapshot.json')
    with pytest.raises(DeliveryError):
        candidate.ReleaseCandidate(runner.runtime,
            runner.runtime.outputs if fault == 'overlap' else runner.outputs,
            runner.receipts, factory=runner.factory, factory_sha256=runner.factory_sha256,
            inputs=runner.inputs, routes=runner.routes, timeout=float('nan') if fault == 'deadline' else 30)
    assert events == []


@pytest.mark.parametrize('fault', ['none', 'identity', 'live', 'field', 'status', 'large', 'invalid'])
def test_http_observation_contract(monkeypatch, fault):
    sha = 'a' * 40
    value = {'commit': sha, 'live_money': False, 'sig': {'version': 'SIG-v1'}}
    if fault == 'identity': value['commit'] = 'b' * 40
    if fault == 'live': value['live_money'] = 0
    if fault == 'field': value['sig']['version'] = 'old'
    raw = json.dumps(value).encode()
    if fault == 'large': raw = b'x' * (1024 * 1024 + 1)
    if fault == 'invalid': raw = b'not json'
    class Response:
        status = 503 if fault == 'status' else 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        offset = 0
        def read1(self, size):
            chunk = raw[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk
    def open_url(url, timeout):
        assert url == 'http://127.0.0.1:43210/health' and timeout == 2
        return Response()
    monkeypatch.setattr(candidate, 'build_opener', lambda *args: SimpleNamespace(open=open_url))
    routes = {'http': {'route': '/health', 'checks': [{'path': ['sig', 'version'], 'value': 'SIG-v1'}]}}
    if fault == 'none':
        assert candidate.probe(43210, sha, routes)['http']['body_sha256'] == candidate.hashlib.sha256(raw).hexdigest()
    else:
        with pytest.raises((DeliveryError, ValueError)): candidate.probe(43210, sha, routes)


def test_incomplete_runtime_prevents_candidate_creation(gate):
    runner, sha, events, _ = gate
    path = runner.runtime.receipts / (sha + '.json')
    value = json.loads(path.read_text())
    value['status'] = 'MEASURING'
    path.write_text(json.dumps(value))
    with pytest.raises(DeliveryError): runner.validate(sha)
    assert not list(runner.receipts.iterdir()) and events == []


def test_candidate_timeout_preserves_cleanup_and_latch(gate, monkeypatch):
    runner, sha, events, _ = gate
    ticks = iter([0, 100])
    monkeypatch.setattr(candidate.time, 'monotonic', lambda: next(ticks))
    def unavailable(*args, **kwargs): raise OSError('not ready')
    monkeypatch.setattr(candidate, 'probe', unavailable)
    with pytest.raises(DeliveryError, match='timed out'): runner.validate(sha)
    assert events[-2:] == ['cleanup', 'socket_closed']
    assert json.loads((runner.receipts / (sha + '.json')).read_text())['status'] == 'CHECKING'


def test_http_trickling_response_cannot_extend_total_deadline(monkeypatch):
    ticks = iter([0, 0, 1, 3])
    monkeypatch.setattr(candidate.time, 'monotonic', lambda: next(ticks))
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read1(self, size): return b' '
    monkeypatch.setattr(candidate, 'build_opener',
        lambda *args: SimpleNamespace(open=lambda *a, **k: Response()))
    with pytest.raises(DeliveryError, match='deadline exceeded'):
        candidate.probe(43210, 'a' * 40,
            {'http': {'route': '/health', 'checks': []}}, deadline=2)
