"""Fail-closed host delivery transaction. No production adapter is enabled here.

The supervisor must supply a trusted host adapter implementing HostOperations.
Requests contain identities only, never commands, paths, or acceptance overrides.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Protocol

BRANCH = 'agent/er1c-free-acquisition'
SHA = re.compile(r'[0-9a-f]{40}')
SOCKET_TESTS = (
    'tests/test_experimental_runtime.py::test_failed_candidate_preserves_previous',
    'tests/test_experimental_runtime.py::test_success_records_identity_without_stopping_child',
)


class DeliveryError(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise DeliveryError(reason)


def atomic_json(path, value):
    """Persist receipts before external mutation, including directory durability."""
    fd, temporary = tempfile.mkstemp(prefix='.delivery-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, sort_keys=True, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _delivery_lease(directory):
    require(directory.is_absolute() and directory.resolve() == directory
            and directory.is_dir(), 'invalid delivery state directory')
    fd = os.open(directory / 'delivery.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeliveryError('concurrent release rejected') from None
        yield
    finally:
        os.close(fd)


@contextmanager
def delivery_lock(directory):
    with _delivery_lease(directory):
        require(not os.path.lexists(directory / 'writer-active.json'),
                'writer containment requires host reconciliation')
        yield


class GitSource:
    """Push only the checked SHA through the existing SSH host alias."""
    def __init__(self, repository):
        self.repository = repository

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repository), *args],
                                       text=True, timeout=120).strip()

    def check(self, expected):
        require(self.git('symbolic-ref', '--short', 'HEAD') == BRANCH, 'wrong branch')
        require(not self.git('status', '--porcelain', '--untracked-files=all'), 'dirty tree')
        require(self.git('rev-parse', 'HEAD') == expected, 'source HEAD mismatch')
        # Reject alternate push URLs and multiple remotes; do not read key material.
        urls = self.git('remote', 'get-url', '--all', 'origin').splitlines()
        push_urls = self.git('remote', 'get-url', '--push', '--all', 'origin').splitlines()
        require(len(urls) == 1 and urls == push_urls and
                re.fullmatch(r'git@github-multimarket:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git', urls[0]),
                'origin must use existing github-multimarket deploy-key alias')

    def push(self, expected):
        self.check(expected)
        self.git('push', 'origin', f'{expected}:refs/heads/{BRANCH}')
        observed = self.git('ls-remote', '--exit-code', 'origin', f'refs/heads/{BRANCH}')
        require(observed.split() == [expected, f'refs/heads/{BRANCH}'], 'origin mismatch')
        self.check(expected)


class HostOperations(Protocol):
    """Trusted host implementation, not loaded from a release request.

    prepare: immutable SHA release plus isolated pinned interpreter. validate:
    full suite including SOCKET_TESTS on exact source, runtime compatibility,
    reporting-only diff policy, SIG-v1 and full-scale runtime/RSS evidence.
    candidate: owned spare-port process, copied state, actual HTTP/API checks.
    snapshot: verified previous release and exact schedule restore data.
    cutover: atomic routing/pointer switch. adopt_schedule: release-local
    interpreter only. restore methods must be idempotent, including partial
    failures. cleanup must stop only this transaction's candidate process.
    """
    def prepare(self, sha): ...
    def validate(self, sha): ...
    def candidate(self, sha): ...
    def snapshot(self): ...
    def cutover(self, sha): ...
    def adopt_schedule(self, sha): ...
    def production(self, sha): ...
    def restore_release(self, snapshot): ...
    def restore_schedule(self, snapshot): ...
    def rollback_check(self, snapshot): ...
    def cleanup(self, sha): ...


def acceptance(evidence, sha, scheduler=False, require_sig=True):
    require(isinstance(evidence, dict), 'missing acceptance evidence')
    require(evidence.get('commit') == sha, 'release identity mismatch')
    require(evidence.get('live_money') is False, 'LIVE_MONEY violation')
    for key in ('http', 'api', 'runtime_compatible') + (('sig_v1',) if require_sig else ()):
        require(evidence.get(key) is True, f'{key} gate failed')
    if scheduler:
        require(evidence.get('scheduler_target') == sha and
                evidence.get('scheduler_verified') is True, 'scheduler gate failed')


def deliver(request, state: Path, source, host: HostOperations, now=None):
    """Execute once under lock. An interrupted transaction requires recovery first.

    Boolean evidence must come from trusted adapter observations, never requests.
    This core is unit-testable without sockets, production access, or credentials.
    """
    now = now or datetime.now(timezone.utc)
    with delivery_lock(state):
        require(set(request) == {'schema', 'request_id', 'head', 'created_at', 'live_money',
                                 'scope'}, 'invalid request fields')
        require(request['schema'] == 'delivery-request-v1' and
                request['scope'] == 'SIG-v1-reporting-only' and
                request['live_money'] is False, 'unauthorized delivery scope')
        sha = request['head']
        require(isinstance(sha, str) and SHA.fullmatch(sha), 'full SHA required')
        identifier = request['request_id']
        require(isinstance(identifier, str) and
                re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier) and identifier != 'delivery',
                'invalid request id')
        try:
            created = datetime.fromisoformat(request['created_at'])
            age = (now - created).total_seconds()
        except (ValueError, TypeError):
            raise DeliveryError('invalid request timestamp') from None
        require(0 <= age <= 900, 'stale or future request')
        journal = state / 'delivery.json'
        if journal.exists():
            previous = json.loads(journal.read_text())
            require(previous.get('status') in ('SUCCEEDED', 'FAILED', 'ROLLED_BACK'),
                    'unresolved delivery requires recovery')
        receipt = state / (identifier + '.json')
        require(not receipt.exists(), 'replayed request')
        record = {'schema': 'delivery-state-v1', 'request_id': identifier,
                  'source_head': sha, 'status': 'STARTED', 'live_money': False,
                  'origin_sync': 'UNKNOWN', 'deployed_release': None,
                  'candidate_validation': None, 'tests': None, 'scheduler_target': None,
                  'blocker': None, 'started_at': now.isoformat()}

        def save(status):
            record['status'] = status
            atomic_json(journal, record)
            atomic_json(receipt, record)

        save('STARTED')
        touched = False
        snapshot = None
        try:
            source.check(sha)
            source.push(sha)
            record['origin_sync'] = 'VERIFIED'
            save('PREPARING')
            host.prepare(sha)
            tests = host.validate(sha)
            require(isinstance(tests, dict) and tests.get('commit') == sha and
                    tests.get('full_suite') is True and tests.get('reporting_only') is True and
                    tests.get('runtime_rss') is True and
                    isinstance(tests.get('socket_tests'), dict) and
                    set(tests['socket_tests']) == set(SOCKET_TESTS) and
                    all(tests['socket_tests'][test] is True for test in SOCKET_TESTS),
                    'required host validation failed')
            record['tests'] = tests
            candidate = host.candidate(sha)
            acceptance(candidate, sha)
            record['candidate_validation'] = candidate
            snapshot = host.snapshot()
            require(isinstance(snapshot, dict) and
                    isinstance(snapshot.get('commit'), str) and
                    SHA.fullmatch(snapshot['commit']) and snapshot.get('verified') is True and
                    isinstance(snapshot.get('schedule'), str), 'verified rollback state required')
            record['rollback'] = snapshot
            save('CUTOVER_PENDING')
            # Recheck after long validation, before any production mutation.
            source.check(sha)
            touched = True
            host.cutover(sha)
            host.adopt_schedule(sha)
            production = host.production(sha)
            acceptance(production, sha, scheduler=True)
            record['deployed_release'] = sha
            record['scheduler_target'] = sha
            record['production_validation'] = production
            host.cleanup(sha)
        except Exception as error:
            record['blocker'] = type(error).__name__ + ': ' + str(error)
            record['deployed_release'] = None
            record['scheduler_target'] = None
            failures = []
            if touched:
                # Attempt both restores even when one fails; never mask failure.
                for operation in (host.restore_release, host.restore_schedule):
                    try:
                        operation(snapshot)
                    except Exception as rollback_error:
                        failures.append(type(rollback_error).__name__)
                try:
                    rollback = host.rollback_check(snapshot)
                    acceptance(rollback, snapshot['commit'],
                               scheduler=True, require_sig=False)
                    record['rollback_validation'] = rollback
                except Exception as rollback_error:
                    failures.append(type(rollback_error).__name__)
            try:
                host.cleanup(sha)
            except Exception as cleanup_error:
                failures.append(type(cleanup_error).__name__)
            record['recovery_errors'] = failures
            if touched and not failures:
                record['deployed_release'] = snapshot['commit']
                record['scheduler_target'] = snapshot['commit']
            save('RECOVERY_REQUIRED' if failures else 'ROLLED_BACK' if touched else 'FAILED')
            raise DeliveryError(record['status'] + ': ' + record['blocker']) from error
        # All host mutations are complete. A receipt write failure after the
        # terminal journal must not start an unjournaled rollback.
        save('SUCCEEDED')
        return record


def recover(state: Path, host: HostOperations):
    """Reconcile an interrupted delivery before the supervisor starts more work.

    Never resume forward delivery: conservatively restore the journaled release
    and schedule when cutover might have happened. Host restore/cleanup operations
    must be idempotent and identify resources by the persisted transaction SHA.
    No source checkout, push, release preparation or strategy promotion occurs.
    """
    with delivery_lock(state):
        journal = state / 'delivery.json'
        require(journal.is_file() and not journal.is_symlink(), 'missing or unsafe recovery journal')
        record = json.loads(journal.read_text())
        require(isinstance(record, dict) and record.get('schema') == 'delivery-state-v1'
                and record.get('live_money') is False, 'invalid recovery journal')
        identifier = record.get('request_id')
        sha = record.get('source_head')
        require(isinstance(identifier, str) and
                re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier) and identifier != 'delivery',
                'invalid recovery request id')
        require(isinstance(sha, str) and SHA.fullmatch(sha), 'invalid recovery SHA')
        receipt = state / (identifier + '.json')
        require(not receipt.is_symlink(), 'unsafe recovery receipt')
        phase = record.get('status')
        require(phase in ('STARTED', 'PREPARING', 'CUTOVER_PENDING', 'RECOVERING',
                          'RECOVERY_REQUIRED', 'SUCCEEDED', 'FAILED', 'ROLLED_BACK'),
                'unknown recovery phase')
        if phase in ('SUCCEEDED', 'FAILED', 'ROLLED_BACK'):
            # Journal is authoritative; repair a receipt left behind by a crash.
            atomic_json(receipt, record)
            return record
        snapshot = record.get('rollback')
        # A persisted backup is sufficient to assume mutation, even if the
        # process died just before cutover. Re-restoration is intentionally safe.
        touched = snapshot is not None or phase == 'CUTOVER_PENDING'
        if touched:
            require(isinstance(snapshot, dict) and
                    isinstance(snapshot.get('commit'), str) and SHA.fullmatch(snapshot['commit'])
                    and snapshot.get('verified') is True and
                    isinstance(snapshot.get('schedule'), str), 'invalid recovery rollback state')

        def save(status):
            record['status'] = status
            # Authoritative journal first: a crash between writes must never
            # leave a terminal journal hiding a new recovery obligation.
            atomic_json(journal, record)
            atomic_json(receipt, record)

        record['deployed_release'] = None
        record['scheduler_target'] = None
        record['blocker'] = 'interrupted delivery; conservative recovery'
        save('RECOVERING')
        failures = []
        if touched:
            for operation in (host.restore_release, host.restore_schedule):
                try:
                    operation(snapshot)
                except Exception as error:
                    failures.append(type(error).__name__)
            try:
                result = host.rollback_check(snapshot)
                acceptance(result, snapshot['commit'], scheduler=True, require_sig=False)
                record['rollback_validation'] = result
            except Exception as error:
                failures.append(type(error).__name__)
        try:
            host.cleanup(sha)
        except Exception as error:
            failures.append(type(error).__name__)
        record['recovery_errors'] = failures
        if touched and not failures:
            record['deployed_release'] = snapshot['commit']
            record['scheduler_target'] = snapshot['commit']
        save('RECOVERY_REQUIRED' if failures else 'ROLLED_BACK' if touched else 'FAILED')
        if failures:
            raise DeliveryError('RECOVERY_REQUIRED: interrupted delivery recovery failed')
        return record
