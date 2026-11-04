"""Host-owned routing/schedule transaction; no production paths or CLI defaults.

Use only under the supervisor delivery lease, with protected state/routing roots.
The injected services driver must verify cgroup/filesystem containment, stage a
persistent release service, observe HTTP/API, and read/install the exact schedule.
It is trusted host configuration, never imported from candidate source or requests.
Routing is a durable atomic JSON pointer consumed by the host routing service.
"""
import hashlib
import json

from tools.delivery_runner import SHA, acceptance, atomic_json, require


class ProductionBackend:
    def __init__(self, state, route, services):
        self.state, self.route, self.services = state, route, services
        for directory in (state, route.parent):
            require(directory.is_absolute() and directory.resolve() == directory
                    and directory.is_dir(), 'unsafe production directory')
        require(state != route.parent and state not in route.parents
                and route.parent not in state.parents, 'production roots must be disjoint')
        self.backup = state / 'previous.json'

    def _sha(self, sha):
        require(isinstance(sha, str) and SHA.fullmatch(sha), 'full SHA required')

    def _read(self, path):
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1048576,
                'missing safe production state')
        value = json.loads(path.read_text())
        require(isinstance(value, dict), 'invalid production state')
        return value

    def _route(self):
        value = self._read(self.route)
        require(set(value) == {'commit', 'live_money'} and value['live_money'] is False,
                'unsafe route identity')
        self._sha(value['commit'])
        return value

    def assert_contained(self, sha):
        self._sha(sha)
        require(self.services.contained(sha) is True, 'host containment unverified')

    def _observe(self, sha, *, sig=True):
        self.assert_contained(sha)
        require(self._route()['commit'] == sha, 'routing identity mismatch')
        result = self.services.observe(sha)
        acceptance(result, sha, require_sig=sig)
        return result

    def _schedule(self):
        value = self.services.read_schedule()
        require(isinstance(value, str) and len(value.encode()) <= 1048576,
                'invalid schedule snapshot')
        return value

    def _verified_schedule(self, sha, schedule):
        require(self._schedule() == schedule and
                self.services.verify_schedule(sha, schedule) is True,
                'scheduler target or interpreter unverified')

    def snapshot(self):
        sha = self._route()['commit']
        self._observe(sha, sig=False)
        schedule = self._schedule()
        self._verified_schedule(sha, schedule)
        require(self._route()['commit'] == sha and self._schedule() == schedule,
                'production changed during snapshot')
        # A preceding transaction must have completed/recovered under the lease.
        value = dict(commit=sha, verified=True, schedule=schedule,
                     schedule_sha256=hashlib.sha256(schedule.encode()).hexdigest())
        atomic_json(self.backup, value)
        require(self._read(self.backup) == value, 'rollback snapshot not durable')
        return value

    def _backup(self, supplied=None):
        value = self._read(self.backup)
        require(set(value) == {'commit', 'verified', 'schedule', 'schedule_sha256'}
                and value['verified'] is True and isinstance(value['schedule'], str),
                'invalid rollback snapshot')
        self._sha(value['commit'])
        require(hashlib.sha256(value['schedule'].encode()).hexdigest() == value['schedule_sha256'],
                'rollback schedule corrupted')
        if supplied is not None:
            require(supplied == value, 'rollback snapshot mismatch')
        return value

    def cutover(self, sha):
        self.assert_contained(sha)
        backup = self._backup()
        require(self._route()['commit'] == backup['commit'], 'route changed since snapshot')
        self._verified_schedule(backup['commit'], backup['schedule'])
        # Service preparation must leave the old service alive for rollback.
        acceptance(self.services.stage(sha), sha)
        self.assert_contained(sha)
        require(self._route()['commit'] == backup['commit'] and
                self._schedule() == backup['schedule'], 'production changed during staging')
        atomic_json(self.route, dict(commit=sha, live_money=False))
        self._observe(sha)

    def adopt_schedule(self, sha):
        self._observe(sha)
        backup = self._backup()
        schedule = self.services.release_schedule(sha)
        require(isinstance(schedule, str) and schedule and
                self.services.verify_schedule(sha, schedule) is True,
                'release-local schedule required')
        require(self._schedule() == backup['schedule'], 'schedule changed since snapshot')
        self.services.install_schedule(schedule)
        self._verified_schedule(sha, schedule)

    def production(self, sha):
        result = self._observe(sha)
        self._verified_schedule(sha, self._schedule())
        return {**result, 'scheduler_target': sha, 'scheduler_verified': True}

    def restore_release(self, snapshot):
        backup = self._backup(snapshot)
        sha = backup['commit']
        self.assert_contained(sha)
        acceptance(self.services.stage(sha), sha, require_sig=False)
        atomic_json(self.route, dict(commit=sha, live_money=False))
        self._observe(sha, sig=False)

    def restore_schedule(self, snapshot):
        backup = self._backup(snapshot)
        # Always try schedule restoration independently of routing restoration.
        self.services.install_schedule(backup['schedule'])
        self._verified_schedule(backup['commit'], backup['schedule'])

    def rollback_check(self, snapshot):
        backup = self._backup(snapshot)
        result = self._observe(backup['commit'], sig=False)
        self._verified_schedule(backup['commit'], backup['schedule'])
        return {**result, 'scheduler_target': backup['commit'], 'scheduler_verified': True}

    def cleanup(self, sha):
        self._sha(sha)
        # Driver may remove only temporary validation resources, never persistent
        # serving/previous-release resources. Quiescence has the same scope.
        self.services.cleanup_validation(sha)
        require(self.quiescent(sha), 'validation containment unresolved')

    def quiescent(self, sha):
        self._sha(sha)
        return self.services.validation_quiescent(sha) is True
