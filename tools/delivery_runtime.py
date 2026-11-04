"""Host runtime/RSS gate; requires protected policy, inputs and cgroup containment.

No production paths, policy defaults, installation or deployment authority.
GNU time maximum RSS is a process high-water measure, not aggregate cgroup RAM.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path

from tools import delivery_environment as environment, preview_release
from tools.delivery_process import run_owned
from tools.delivery_runner import atomic_json, require

LAUNCH = '''import runpy, sys
source, script = sys.argv[1:3]
sys.path.insert(0, source)
sys.argv = [script] + sys.argv[3:]
runpy.run_path(script, run_name="__main__")
'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ReleaseRuntime:
    def __init__(self, tests, outputs, receipts, *, timer, timer_sha256, workloads):
        self.tests, self.outputs, self.receipts = tests, outputs, receipts
        self.timer, self.timer_sha256 = timer, timer_sha256
        self.workloads = copy.deepcopy(workloads)
        roots = (outputs, receipts, tests.outputs, tests.receipts,
                 tests.prepared.repository, tests.prepared.bundles,
                 tests.prepared.environments, tests.prepared.wheelhouse, tests.prepared.receipts)
        for path in roots:
            preview_release._absolute_directory(path, 'runtime directory')
        require(all(a != b and a not in b.parents and b not in a.parents
                    for i, a in enumerate(roots) for b in roots[i + 1:]),
                'runtime directories must be disjoint')
        require(isinstance(workloads, dict) and set(workloads) == {'EGX', 'US'},
                'both full-scale market workloads required')
        for policy in self.workloads.values():
            require(isinstance(policy, dict) and set(policy) ==
                    {'script', 'args', 'inputs', 'max_seconds', 'max_rss_kib'},
                    'invalid workload policy')
            script = Path(policy['script'])
            require(not script.is_absolute() and '..' not in script.parts and script.suffix == '.py',
                    'invalid workload script')
            require(isinstance(policy['args'], list) and all(isinstance(a, str) and '\0' not in a
                    for a in policy['args']), 'invalid workload arguments')
            require(isinstance(policy['inputs'], dict) and policy['inputs'], 'pinned snapshot inputs required')
            for key in ('max_seconds', 'max_rss_kib'):
                value = policy[key]
                require(type(value) in (int, float) and math.isfinite(value) and value > 0,
                        'invalid runtime budget')
        self._pins()

    def _pins(self):
        for name, expected in [(str(self.timer), self.timer_sha256)] + [
                item for policy in self.workloads.values() for item in policy['inputs'].items()]:
            path = Path(name)
            require(path.is_absolute() and path.resolve() == path and path.is_file()
                    and environment.digest(path) == expected, 'runtime input or timer mismatch')
        return digest({'timer': str(self.timer), 'timer_sha256': self.timer_sha256,
                       'workloads': self.workloads})

    def _identity(self, sha):
        self.tests.verify(sha)
        return {'schema': 'delivery-runtime-v1', 'commit': sha, 'live_money': False,
                'policy_sha256': self._pins(),
                'test_receipt_sha256': environment.digest(self.tests.receipts / (sha + '.json'))}

    def _metrics(self, path, policy):
        require(path.resolve() == path and path.is_file() and not path.is_symlink()
                and path.stat().st_size <= 1024,
                'missing safe runtime measurements')
        parts = path.read_text().split()
        require(len(parts) == 3, 'invalid runtime measurements')
        seconds, rss, exit_code = float(parts[0]), int(parts[1]), int(parts[2])
        require(math.isfinite(seconds) and 0 <= seconds <= policy['max_seconds']
                and 0 < rss <= policy['max_rss_kib'] and exit_code == 0,
                'runtime budget or exit gate failed')
        return {'seconds': seconds, 'max_rss_kib': rss, 'exit_code': exit_code,
                'measurement_sha256': environment.digest(path)}

    def validate(self, sha):
        identity = self._identity(sha)
        receipt, output = self.receipts / (sha + '.json'), self.outputs / sha
        require(not os.path.lexists(receipt) and not os.path.lexists(output),
                'runtime validation already exists; host reconciliation required')
        started = datetime.now(timezone.utc).isoformat()
        atomic_json(receipt, {**identity, 'status': 'MEASURING', 'started_at': started})
        output.mkdir(mode=0o700)
        source = self.tests.prepared.bundles / sha / 'source'
        interpreter = self.tests.prepared.verify(sha)
        results = {}
        for market, policy in self.workloads.items():
            target = output / market
            target.mkdir(mode=0o700)
            script = source / policy['script']
            require(script.is_file() and not script.is_symlink(), 'workload script missing')
            metrics = target / 'time.txt'
            argv = [str(self.timer), '-f', '%e %M %x', '-o', str(metrics),
                    str(interpreter), '-I', '-B', '-c', LAUNCH, str(source), str(script), *policy['args']]
            env = {'PATH': str(interpreter.parent) + ':/usr/bin:/bin', 'HOME': str(target),
                   'TMPDIR': str(target), 'LANG': 'C.UTF-8', 'LIVE_MONEY': 'false',
                   'PYTHONDONTWRITEBYTECODE': '1'}
            try:
                run_owned(argv, cwd=target, timeout=policy['max_seconds'], env=env)
            finally:
                require(self._identity(sha) == identity, 'runtime evidence changed during measurement')
            results[market] = self._metrics(metrics, policy)
            with metrics.open('rb') as stream:
                os.fsync(stream.fileno())
            fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        for directory in (output, self.outputs):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        result = {**identity, 'status': 'MEASURED', 'started_at': started,
                  'completed_at': datetime.now(timezone.utc).isoformat(), 'markets': results}
        atomic_json(receipt, result)
        return self.verify(sha)

    def verify(self, sha):
        identity = self._identity(sha)
        path = self.receipts / (sha + '.json')
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384,
                'missing safe runtime receipt')
        value = json.loads(path.read_text())
        require(isinstance(value, dict) and value.get('live_money') is False,
                'invalid runtime receipt')
        expected = {**identity, 'status': 'MEASURED', 'markets': {
            market: self._metrics(self.outputs / sha / market / 'time.txt', policy)
            for market, policy in self.workloads.items()}}
        require(set(value) == set(expected) | {'started_at', 'completed_at'}
                and all(digest(value[k]) == digest(v) for k, v in expected.items()),
                'runtime receipt mismatch')
        start, end = (datetime.fromisoformat(value[k]) for k in ('started_at', 'completed_at'))
        require(start.tzinfo is not None and end.tzinfo is not None and end >= start,
                'invalid runtime timestamps')
        return {**self.tests.verify(sha), 'runtime_rss': True, 'runtime_evidence': value}
