"""Trusted host exact-release tests; no deployment authority or production defaults.

Caller holds the delivery lease and provides protected runtime pins and roots.
Execute only inside verified host cgroup containment with immutable OS/runtime.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from tools import delivery_environment as environment, preview_release
from tools.delivery_runner import SOCKET_TESTS, atomic_json, require

LAUNCH = '''import sys
sys.path.insert(0, sys.argv[1])
import pytest
raise SystemExit(pytest.main(sys.argv[2:]))
'''


def report(path):
    require(path.resolve() == path and path.is_file() and not path.is_symlink()
            and path.stat().st_size <= 64 * 1024 * 1024,
            'missing or unsafe test report')
    try:
        root = ET.fromstring(path.read_bytes())
    except ET.ParseError as exc:
        raise ValueError('invalid test report') from exc
    require(root.tag == 'testsuites', 'invalid test report root')
    suites = list(root.iter('testsuite'))
    require(suites and all(s.get('failures', '0') == '0' and s.get('errors', '0') == '0'
                           for s in suites), 'test suite reports failures')
    cases = list(root.iter('testcase'))
    require(cases and not list(root.iter('failure')) and not list(root.iter('error')),
            'test report contains failures or no tests')
    sockets = {}
    for nodeid in SOCKET_TESTS:
        filename, name = nodeid.split('::')
        classname = filename.removesuffix('.py').replace('/', '.')
        matches = [c for c in cases if c.get('classname') == classname and c.get('name') == name]
        require(len(matches) == 1 and not list(matches[0]),
                'required socket test missing, skipped or ambiguous')
        sockets[nodeid] = True
    return {'testcases': len(cases), 'skipped': len(list(root.iter('skipped'))),
            'socket_tests': sockets, 'report_sha256': environment.digest(path)}


class ReleaseTests:
    def __init__(self, prepared, outputs, receipts, *, runtime_files, launcher=None):
        self.launcher = launcher
        self.prepared = prepared
        self.outputs = outputs
        self.receipts = receipts
        self.runtime_files = copy.deepcopy(runtime_files)
        roots = (outputs, receipts, prepared.repository, prepared.bundles,
                 prepared.environments, prepared.wheelhouse, prepared.receipts)
        for path in roots:
            preview_release._absolute_directory(path, 'validation directory')
        require(all(a != b and a not in b.parents and b not in a.parents
                    for i, a in enumerate(roots) for b in roots[i + 1:]),
                'validation directories must be disjoint')
        self._runtime()

    def _runtime(self):
        require(isinstance(self.runtime_files, dict) and self.runtime_files,
                'host runtime pins required')
        for name, expected in self.runtime_files.items():
            path = Path(name)
            require(path.is_absolute() and path.resolve() == path and path.is_file()
                    and environment.digest(path) == expected, 'host runtime mismatch')
        return hashlib.sha256(json.dumps(self.runtime_files, sort_keys=True).encode()).hexdigest()

    def _command(self, sha, interpreter):
        output = self.outputs / sha
        return [str(interpreter), '-I', '-B', '-c', LAUNCH,
                str(self.prepared.bundles / sha / 'source'), 'tests', '-o', 'addopts=',
                '-p', 'no:cacheprovider', '--basetemp=' + str(output / 'tmp'),
                '--junitxml=' + str(output / 'tests.xml')]

    def verify(self, sha):
        """Revalidate durable test evidence before downstream acceptance/restart."""
        interpreter = self.prepared.verify(sha)
        path = self.receipts / (sha + '.json')
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 65536,
                'missing safe test receipt')
        value = json.loads(path.read_text())
        require(isinstance(value, dict), 'invalid test receipt')
        expected = {'schema': 'delivery-tests-v1', 'commit': sha,
                    'runtime_policy_sha256': self._runtime(), 'live_money': False,
                    'preparation_receipt_sha256': environment.digest(
                        self.prepared.receipts / (sha + '.json')),
                    'runtime_rss': False, 'candidate_validation': 'NOT_RUN',
                    **report(self.outputs / sha / 'tests.xml'),
                    'status': 'TESTED', 'full_suite': True, 'reporting_only': True,
                    'command': self._command(sha, interpreter)}
        require(set(value) == set(expected) | {'started_at', 'completed_at'},
                'test receipt fields mismatch')
        require(all(json.dumps(value[k], sort_keys=True) == json.dumps(v, sort_keys=True)
                    for k, v in expected.items()), 'test receipt mismatch')
        start, end = (datetime.fromisoformat(value[k]) for k in ('started_at', 'completed_at'))
        require(start.tzinfo is not None and end.tzinfo is not None and end >= start,
                'invalid test receipt timestamps')
        return value

    def validate(self, sha, *, timeout=1800):
        require(self.launcher is not None, 'protected tests launcher required')
        interpreter = self.prepared.verify(sha)
        runtime = self._runtime()
        receipt = self.receipts / (sha + '.json')
        output = self.outputs / sha
        require(not os.path.lexists(receipt) and not os.path.lexists(output),
                'validation already exists; host reconciliation required')
        identity = {'schema': 'delivery-tests-v1', 'commit': sha,
                    'runtime_policy_sha256': runtime, 'live_money': False,
                    'preparation_receipt_sha256': environment.digest(
                        self.prepared.receipts / (sha + '.json')),
                    'started_at': datetime.now(timezone.utc).isoformat(),
                    'runtime_rss': False, 'candidate_validation': 'NOT_RUN'}
        atomic_json(receipt, {**identity, 'status': 'TESTING'})
        output.mkdir(mode=0o700)
        source = self.prepared.bundles / sha / 'source'
        junit = output / 'tests.xml'
        argv = self._command(sha, interpreter)
        env = {'PATH': str(interpreter.parent) + ':/usr/bin:/bin',
               'HOME': str(output), 'TMPDIR': str(output), 'LANG': 'C.UTF-8',
               'LIVE_MONEY': 'false', 'PYTHONDONTWRITEBYTECODE': '1'}
        # On any error/interruption retain TESTING. Never adopt interrupted evidence.
        try:
            self.launcher.run('tests', argv, cwd=source, timeout=timeout, env=env)
        finally:
            self.prepared.verify(sha)
            require(self._runtime() == runtime, 'host runtime changed')
        evidence = report(junit)
        with junit.open('rb') as stream:
            os.fsync(stream.fileno())
        fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        result = {**identity, **evidence, 'status': 'TESTED', 'full_suite': True,
                  'completed_at': datetime.now(timezone.utc).isoformat(),
                  'reporting_only': True, 'command': argv}
        atomic_json(receipt, result)
        return result
