"""Host spare-port acceptance. No production adapter or installation defaults.

Requires an enclosing delivery lock, protected policy/receipts and verified host
cgroup/filesystem containment. The pinned host factory wires the exact source to
copied state; its contract and HTTP assertions require independent host review.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import time
from urllib.request import build_opener, ProxyHandler

from tools import delivery_environment as environment, preview_release
from tools.delivery_runtime import digest
from tools.delivery_runner import atomic_json, require, acceptance
from tools.experimental_runtime import NoRedirect

BOOT = '''import runpy, socket, sys
sys.path.insert(0, sys.argv[1])
import uvicorn
factory = runpy.run_path(sys.argv[2])["create_app"]
app = factory(source=sys.argv[1], state=sys.argv[3], commit=sys.argv[4])
uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="warning")).run(
    sockets=[socket.socket(fileno=int(sys.argv[5]))])
'''


def probe(port, sha, routes, *, deadline=None):
    """Observe bounded localhost responses without proxies or redirects."""
    opener = build_opener(ProxyHandler({}), NoRedirect())
    observations = {}
    def remaining():
        budget = 2 if deadline is None else min(2, deadline - time.monotonic())
        require(budget > 0, 'candidate acceptance deadline exceeded')
        return budget
    for gate, policy in routes.items():
        with opener.open(f'http://127.0.0.1:{port}' + policy['route'], timeout=remaining()) as response:
            chunks, size = [], 0
            while size <= 1024 * 1024:
                remaining()
                # One underlying read prevents a trickling peer from keeping
                # read() blocked indefinitely by resetting the socket timeout.
                chunk = response.read1(min(65536, 1024 * 1024 + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            remaining()
            raw = b''.join(chunks)
            require(response.status == 200 and len(raw) <= 1024 * 1024,
                    'candidate HTTP response rejected')
        value = json.loads(raw)
        if gate in ('http', 'api'):
            require(isinstance(value, dict) and value.get('commit') == sha
                    and value.get('live_money') is False, 'candidate identity or LIVE_MONEY violation')
        for check in policy['checks']:
            observed = value
            for key in check['path']:
                require(isinstance(observed, dict) and key in observed, 'candidate field missing')
                observed = observed[key]
            require(digest(observed) == digest(check['value']), 'candidate field mismatch')
        observations[gate] = {'route': policy['route'], 'body_sha256': hashlib.sha256(raw).hexdigest()}
    return observations


class ReleaseCandidate:
    def __init__(self, runtime, outputs, receipts, *, factory, factory_sha256, inputs,
                 routes, timeout=30, launcher=None):
        self.launcher = launcher
        self.runtime, self.outputs, self.receipts = runtime, outputs, receipts
        self.factory, self.factory_sha256 = factory, factory_sha256
        self.inputs, self.routes = copy.deepcopy(inputs), copy.deepcopy(routes)
        self.timeout = timeout
        tests, prepared = runtime.tests, runtime.tests.prepared
        roots = (outputs, receipts, runtime.outputs, runtime.receipts, tests.outputs,
                 tests.receipts, prepared.repository, prepared.bundles,
                 prepared.environments, prepared.wheelhouse, prepared.receipts)
        for path in roots:
            preview_release._absolute_directory(path, 'candidate directory')
        require(all(a != b and a not in b.parents and b not in a.parents
                    for i, a in enumerate(roots) for b in roots[i + 1:]),
                'candidate directories must be disjoint')
        require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
                'invalid candidate deadline')
        require(isinstance(inputs, dict) and inputs, 'candidate snapshot inputs required')
        for name, item in self.inputs.items():
            path = Path(name)
            require(name and not path.is_absolute() and '..' not in path.parts and path.parts
                    and isinstance(item, dict) and set(item) == {'path', 'sha256'},
                    'invalid candidate input')
        require(isinstance(routes, dict) and set(routes) == {'http', 'api', 'runtime_compatible', 'sig_v1'},
                'all candidate gates required')
        for policy in self.routes.values():
            require(isinstance(policy, dict) and set(policy) == {'route', 'checks'}
                    and isinstance(policy['route'], str) and policy['route'].startswith('/')
                    and not policy['route'].startswith('//')
                    and not any(c in policy['route'] for c in ('\r', '\n', '#'))
                    and isinstance(policy['checks'], list) and policy['checks'], 'invalid HTTP gate policy')
            for check in policy['checks']:
                require(isinstance(check, dict) and set(check) == {'path', 'value'}
                        and isinstance(check['path'], list) and check['path']
                        and all(isinstance(k, str) and k for k in check['path']), 'invalid field check')
        self._pins()

    def _pins(self):
        for item in [{'path': str(self.factory), 'sha256': self.factory_sha256}, *self.inputs.values()]:
            path = Path(item['path'])
            require(path.is_absolute() and path.resolve() == path and path.is_file()
                    and environment.digest(path) == item['sha256'], 'candidate input or factory mismatch')
        return digest({'factory': str(self.factory), 'factory_sha256': self.factory_sha256,
                       'inputs': self.inputs, 'routes': self.routes, 'timeout': self.timeout,
                       'boot': BOOT})

    def _identity(self, sha):
        self.runtime.verify(sha)
        return {'schema': 'delivery-candidate-v1', 'commit': sha, 'live_money': False,
                'policy_sha256': self._pins(), 'runtime_receipt_sha256': environment.digest(
                    self.runtime.receipts / (sha + '.json'))}

    def validate(self, sha):
        require(self.launcher is not None, 'protected candidate launcher required')
        identity = self._identity(sha)
        receipt, output = self.receipts / (sha + '.json'), self.outputs / sha
        require(not os.path.lexists(receipt) and not os.path.lexists(output),
                'candidate already exists; host reconciliation required')
        started = datetime.now(timezone.utc).isoformat()
        atomic_json(receipt, {**identity, 'status': 'CHECKING', 'started_at': started})
        output.mkdir(mode=0o700)
        state = output / 'state'
        state.mkdir(mode=0o700)
        for name, item in self.inputs.items():
            target = state / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(Path(item['path']).read_bytes())
            require(environment.digest(target) == item['sha256'], 'copied state mismatch')
        prepared = self.runtime.tests.prepared
        interpreter = prepared.verify(sha)
        source = prepared.bundles / sha / 'source'
        env = {'PATH': str(interpreter.parent) + ':/usr/bin:/bin', 'HOME': str(output),
               'TMPDIR': str(output), 'LANG': 'C.UTF-8', 'LIVE_MONEY': 'false',
               'PYTHONDONTWRITEBYTECODE': '1'}
        # Bind an OS-selected spare port before spawn; never reuse a listener.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            require(port not in (8000, 8001), 'production port rejected')
            listener.listen(128)
            argv = [str(interpreter), '-I', '-B', '-c', BOOT, str(source), str(self.factory),
                    str(state), sha, str(listener.fileno())]
            with self.launcher.process('candidate', argv, cwd=output, env=env, pass_fds=(listener.fileno(),)) as process:
                deadline = time.monotonic() + self.timeout
                while True:
                    require(process.poll() is None, 'candidate exited before acceptance')
                    try:
                        observations = probe(port, sha, self.routes, deadline=deadline)
                        break
                    except OSError:
                        require(time.monotonic() < deadline, 'candidate HTTP startup timed out')
                        time.sleep(0.05)
                require(time.monotonic() <= deadline, 'candidate acceptance deadline exceeded')
                require(process.poll() is None, 'candidate exited during acceptance')
                require(self._identity(sha) == identity, 'candidate dependencies changed')
        # Cleanup must succeed before accepted evidence can be published.
        require(self._identity(sha) == identity, 'candidate dependencies changed')
        result = {**identity, 'status': 'ACCEPTED', 'started_at': started,
                  'completed_at': datetime.now(timezone.utc).isoformat(),
                  'port': port, 'observations': observations,
                  **dict.fromkeys(self.routes, True)}
        atomic_json(receipt, result)
        return self.verify(sha)

    def verify(self, sha):
        """Historical acceptance only: never assert a candidate is still serving."""
        identity = self._identity(sha)
        path = self.receipts / (sha + '.json')
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 65536,
                'missing safe candidate receipt')
        value = json.loads(path.read_text())
        require(isinstance(value, dict) and set(value) == set(identity) | {
            'status', 'started_at', 'completed_at', 'port', 'observations', *self.routes},
            'candidate receipt fields mismatch')
        require(all(digest(value[k]) == digest(v) for k, v in identity.items())
                and value['status'] == 'ACCEPTED', 'candidate receipt mismatch')
        acceptance(value, sha)
        require(type(value['port']) is int and 1 <= value['port'] <= 65535
                and value['port'] not in (8000, 8001), 'invalid candidate port')
        observations = value['observations']
        require(isinstance(observations, dict) and set(observations) == set(self.routes),
                'missing candidate observations')
        for gate, item in observations.items():
            require(isinstance(item, dict) and set(item) == {'route', 'body_sha256'}
                    and item['route'] == self.routes[gate]['route']
                    and isinstance(item['body_sha256'], str) and len(item['body_sha256']) == 64
                    and all(c in '0123456789abcdef' for c in item['body_sha256']),
                    'invalid candidate observation')
        start, end = (datetime.fromisoformat(value[k]) for k in ('started_at', 'completed_at'))
        require(start.tzinfo is not None and end.tzinfo is not None and end >= start,
                'invalid candidate timestamps')
        return value
