"""Protected installed-code manifest gate; not an installer or dynamic loader."""
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys

from tools.delivery_containment import _absolute
from tools.delivery_entrypoint import HostEntrypoint, protected_bytes, _unique
from tools.delivery_runner import DeliveryError, require


def _gate_sources():
    """Source of every loaded tools module, so a manifest cannot omit code it trusts."""
    return {Path(_absolute(getattr(module, '__file__', None)))
            for name, module in tuple(sys.modules.items()) if name.startswith('tools.')}


def _factory_source(factory):
    """Bind the factory to the module-level definition loaded from its source file."""
    require(inspect.isfunction(factory), 'installed factory function required')
    module = sys.modules.get(factory.__module__)
    require(factory.__qualname__ == factory.__name__
            and getattr(module, factory.__name__, None) is factory,
            'module-level installed factory required')
    path = Path(_absolute(inspect.getfile(factory)))
    require(getattr(module, '__file__', None) == str(path), 'factory module source mismatch')
    return path


class InstalledBootstrap:
    """The host supplies all arguments from immutable installed code.

    Files must include the factory source, every loaded tools module and any other
    declared Python/policy dependencies. Only those loaded tools modules are enforced;
    the host still owns interpreter/stdlib isolation, bytecode caches and completeness
    for anything else. Pins compare files on disk, not code already imported.
    Never import a factory named by an untrusted request or by this manifest.
    """
    def __init__(self, manifest, digest, factory, *, owner=0):
        require(type(owner) is int and owner >= 0 and os.getuid() == owner
                and os.geteuid() == owner, 'bootstrap identity required')
        self.owner, self.pid = owner, os.getpid()
        self.manifest = Path(_absolute(str(manifest)))
        self.digest, self.factory = digest, factory
        raw = protected_bytes(self.manifest, owner)
        require(hashlib.sha256(raw).hexdigest() == digest, 'bootstrap manifest pin mismatch')
        try:
            policy = json.loads(raw, object_pairs_hook=_unique)
        except (ValueError, RecursionError):
            raise DeliveryError('malformed bootstrap manifest') from None
        require(type(policy) is dict and set(policy) == {
            'schema', 'configuration', 'files', 'live_money'}, 'invalid bootstrap fields')
        require(policy['schema'] == 'delivery-bootstrap-v1' and policy['live_money'] is False,
                'unsafe bootstrap policy')
        files = policy['files']
        require(type(files) is dict and 0 < len(files) <= 1024,
                'invalid bootstrap files')
        self.files = {}
        for name, pin in files.items():
            path = Path(_absolute(name))
            require(type(pin) is str and len(pin) == 64
                    and all(c in '0123456789abcdef' for c in pin), 'invalid bootstrap file pin')
            self.files[path] = pin
        self.configuration = Path(_absolute(policy['configuration']))
        require(self.configuration in self.files, 'configuration not pinned')
        self.factory_path = _factory_source(factory)
        require(self.factory_path in self.files, 'factory source not pinned')
        self._check()
        self.entrypoint = HostEntrypoint(self.configuration, self.files[self.configuration],
                                        factory, owner=owner)
        self._entrypoint = self.entrypoint
        # Reject construction that alters any installed policy/dependency.
        self._check()

    def _check(self):
        require(os.getuid() == self.owner and os.geteuid() == self.owner
                and os.getpid() == self.pid, 'bootstrap identity changed')
        require(hashlib.sha256(protected_bytes(self.manifest, self.owner)).hexdigest()
                == self.digest, 'bootstrap manifest pin mismatch')
        require(_factory_source(self.factory) == self.factory_path,
                'installed factory binding changed')
        require(_gate_sources() <= set(self.files), 'loaded delivery module not pinned')
        for path, pin in self.files.items():
            require(hashlib.sha256(protected_bytes(path, self.owner)).hexdigest() == pin,
                    'installed bootstrap file pin mismatch')

    def invoke(self, operation, request=None):
        self._check()
        require(self.entrypoint is self._entrypoint, 'bootstrap entrypoint changed')
        return self.entrypoint.invoke(operation, request)

    def completion(self, path, digest):
        # Load callback dependencies before verifying complete installed-code pins.
        from tools import delivery_observations  # noqa: F401
        self._check()
        require(self.entrypoint is self._entrypoint, 'bootstrap entrypoint changed')
        return self.entrypoint.completion(path, digest)
