"""Trusted composition for HostOperations; no production defaults or CLI.

Construct from host-owned components under the supervisor lease. The production
backend owns containment reconciliation, atomic routing and schedule restoration.
No request may supply these dependencies. Incomplete evidence is never deleted.
"""
import os

from tools.delivery_runner import SHA, acceptance, require


class ComposedHost:
    def __init__(self, candidate, production):
        self.release_candidate = candidate
        self.runtime = candidate.runtime
        self.tests = self.runtime.tests
        self.prepared = self.tests.prepared
        self.backend = production

    def _components(self):
        return (self.prepared, self.tests, self.runtime, self.release_candidate)

    def _artifacts(self, component, sha):
        if component is self.prepared:
            return (component.bundles / sha, component.environments / sha)
        return (component.outputs / sha,)

    def _sha(self, sha):
        require(isinstance(sha, str) and SHA.fullmatch(sha), 'full SHA required')

    def prepare(self, sha):
        self._sha(sha)
        # Reject downstream orphans before creating any new artifact.
        for component in self._components():
            paths = (component.receipts / (sha + '.json'), *self._artifacts(component, sha))
            require(not any(os.path.lexists(path) for path in paths),
                    'existing release evidence requires host reconciliation')
        self.backend.assert_contained(sha)
        self.prepared.prepare(sha)
        return self.prepared.verify(sha)

    def validate(self, sha):
        self._sha(sha)
        self.backend.assert_contained(sha)
        self.prepared.verify(sha)
        self.tests.validate(sha)
        self.tests.verify(sha)
        self.runtime.validate(sha)
        return self.runtime.verify(sha)

    def candidate(self, sha):
        self._sha(sha)
        self.backend.assert_contained(sha)
        self.runtime.verify(sha)
        self.release_candidate.validate(sha)
        return self.release_candidate.verify(sha)

    def snapshot(self):
        return self.backend.snapshot()

    def _accepted(self, sha):
        self._sha(sha)
        self.backend.assert_contained(sha)
        # Reopens the complete dependency chain immediately before mutation.
        acceptance(self.release_candidate.verify(sha), sha)

    def cutover(self, sha):
        self._accepted(sha)
        return self.backend.cutover(sha)

    def adopt_schedule(self, sha):
        self._accepted(sha)
        return self.backend.adopt_schedule(sha)

    def production(self, sha):
        self._accepted(sha)
        result = self.backend.production(sha)
        acceptance(result, sha, scheduler=True)
        return result

    def restore_release(self, snapshot):
        # Restoration must remain possible when candidate evidence is damaged.
        return self.backend.restore_release(snapshot)

    def restore_schedule(self, snapshot):
        return self.backend.restore_schedule(snapshot)

    def rollback_check(self, snapshot):
        result = self.backend.rollback_check(snapshot)
        acceptance(result, snapshot['commit'], scheduler=True, require_sig=False)
        return result

    def cleanup(self, sha):
        self._sha(sha)
        # Always attempt containment cleanup, even with corrupt/incomplete receipts.
        # Backend must reconcile only transaction-owned resources, never production.
        self.backend.cleanup(sha)
        require(self.backend.quiescent(sha) is True,
                'release containment requires host reconciliation')
        missing = False
        for component in self._components():
            receipt = component.receipts / (sha + '.json')
            if os.path.lexists(receipt):
                require(not missing, 'noncontiguous release evidence')
                component.verify(sha)
            else:
                require(not any(os.path.lexists(p) for p in self._artifacts(component, sha)),
                        'unreceipted release artifact requires host reconciliation')
                missing = True
        # verify rejects incomplete/altered receipts. Do not erase them or bless
        # them after cleanup: the runner persists RECOVERY_REQUIRED on failure.
