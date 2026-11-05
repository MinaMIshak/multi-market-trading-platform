"""Explicit protected-host composition, with no installation or path defaults.

The host constructs ReleaseService with ServiceContainment and a direct-service
probe, and ReleaseSchedule with the same verified release identities. The public
probe must traverse the installed reporting router, not its direct upstream.
Validation cleanup callbacks belong to a separate temporary-job containment
boundary and must never stop persistent serving or rollback services.
"""
from tools.delivery_runner import acceptance, require
from tools.delivery_routing import ReportingRouter


class ProductionServices:
    def __init__(self, service, schedule, public_probe, cleanup_validation,
                 validation_quiescent):
        self.service = service
        self.schedule = schedule
        self.public_probe = public_probe
        self._cleanup = cleanup_validation
        self._quiescent = validation_quiescent

    def router(self, pointer, ports, paths, *, owner=0):
        # Request-time observation must never start a service or recurse through
        # the public probe. Inactive upstreams produce 503 instead of activation.
        return ReportingRouter(pointer, ports, paths, self.service.observe, owner=owner)

    def contained(self, sha):
        return self.service.contained(sha)

    def stage(self, sha):
        return self.service.stage(sha)

    def observe(self, sha):
        acceptance(self.service.observe(sha), sha, require_sig=False)
        result = self.public_probe(sha)
        acceptance(result, sha, require_sig=False)
        # Detect service/isolation drift during the public HTTP/API probe.
        acceptance(self.service.observe(sha), sha, require_sig=False)
        return result

    def read_schedule(self):
        return self.schedule.read_schedule()

    def release_schedule(self, sha):
        return self.schedule.release_schedule(sha)

    def verify_schedule(self, sha, text):
        return self.schedule.verify_schedule(sha, text) is True

    def install_schedule(self, text):
        self.schedule.install_schedule(text)

    def cleanup_validation(self, sha):
        self._cleanup(sha)
        require(self.validation_quiescent(sha), 'temporary job containment unresolved')

    def validation_quiescent(self, sha):
        return self._quiescent(sha) is True
