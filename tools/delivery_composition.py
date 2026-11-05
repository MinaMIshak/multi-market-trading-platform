"""Host-owned assembly only; never load this policy from a release request.

The host must install isolation and exclude uncoordinated admission. Preflight
observes those bindings under the supervisor lease; it does not install them.
"""
from pathlib import Path

from tools.delivery_containment import _absolute, _protected
from tools.delivery_jobs import JobContainment, JobLauncher
from tools.delivery_host import ComposedHost
from tools.delivery_process import CleanupError
from tools.delivery_runner import SHA, require
from tools.delivery_supervisor import DeliverySupervisor


RELEASE_JOBS = ('environment', 'tests', 'runtime', 'candidate')
ALL_JOBS = ('writer', 'bridge', *RELEASE_JOBS)


class HostJobs:
    def __init__(self, containment, *, python, persistent_groups, isolation):
        require(type(containment) is JobContainment, 'host job containment required')
        self.containment = containment
        self.launcher = JobLauncher(containment, python=python)
        self.persistent_groups = tuple(persistent_groups)
        self.isolation = isolation
        self._policy = dict(containment.policies)
        self.validate_policy()

    def validate_policy(self):
        require(set(self.containment.policies) == set(ALL_JOBS)
                and self.containment.policies == self._policy,
                'exact stable host job policy required')
        require(self.persistent_groups, 'persistent service groups required')
        protected = [Path(_absolute(p)) for p in self.persistent_groups]
        temporary = [Path(_absolute(p)) for p in self._policy.values()]
        require(all(a != b and not a.is_relative_to(b) and not b.is_relative_to(a)
                    for a in temporary for b in protected),
                'temporary and persistent groups overlap')
        require(all(a != b and not a.is_relative_to(b) and not b.is_relative_to(a)
                    for i, a in enumerate(temporary) for b in temporary[i + 1:]),
                'overlapping temporary cgroups')

    def preflight(self):
        self.validate_policy()
        python = self.launcher.python
        require(python.is_absolute() and python.resolve() == python and python.is_file(),
                'invalid bootstrap interpreter')
        _protected(python, self.containment.owner)
        require(self.isolation() is True, 'host job isolation unverified')
        for key in ALL_JOBS:
            require(self.containment.quiescent(key) is True, 'host job still active')

    def cleanup(self, sha):
        require(isinstance(sha, str) and SHA.fullmatch(sha), 'full SHA required')
        self.validate_policy()
        # Try every release stage even if an earlier stage cannot be reconciled.
        # Never kill writer/bridge or persistent services during release recovery.
        failures = []
        for key in RELEASE_JOBS:
            try:
                self.containment.cleanup(key)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise CleanupError('aggregate release cleanup unconfirmed') from failures[0]

    def quiescent(self, sha):
        require(isinstance(sha, str) and SHA.fullmatch(sha), 'full SHA required')
        self.validate_policy()
        observations = [self.containment.quiescent(key) for key in RELEASE_JOBS]
        return all(value is True for value in observations)


def compose_supervisor(state, source, candidate, production, jobs):
    """Bind one policy to every launch and recovery path before accepting work."""
    host = ComposedHost(candidate, production)
    components = host._components()
    services = production.services
    for component in components:
        require(component.launcher is None or component.launcher is jobs.launcher,
                'conflicting component launcher')
    for component in components:
        component.launcher = jobs.launcher
    services._cleanup = jobs.cleanup
    services._quiescent = jobs.quiescent

    def preflight():
        _protected(state, jobs.containment.owner)
        require(supervisor.launcher is jobs.launcher
                and all(c.launcher is jobs.launcher for c in components)
                and services._cleanup == jobs.cleanup
                and services._quiescent == jobs.quiescent,
                'host composition changed')
        jobs.preflight()

    supervisor = DeliverySupervisor(state, source, host, launcher=jobs.launcher,
                                    preflight=preflight)
    return supervisor
