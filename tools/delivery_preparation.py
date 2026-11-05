"""Trusted host preparation component, called inside the delivery lease.

No production defaults or CLI. Policy and receipt roots must be host protected;
this component does not attest host runtime, tests, or candidate acceptance.
"""
import copy
import hashlib
import json
import os
import re

from tools import delivery_bundle as bundle, delivery_environment as environment
from tools import preview_release
from tools.delivery_runner import atomic_json, require

SCHEMA = 'delivery-preparation-v1'


class PreparedRelease:
    def __init__(self, repository, bundles, environments, wheelhouse, receipts, *,
                 baseline, approved_changes, policy, launcher=None):
        self.launcher = launcher
        self.repository = repository
        self.bundles = bundles
        self.environments = environments
        self.wheelhouse = wheelhouse
        self.receipts = receipts
        self.baseline = baseline
        self.changes = copy.deepcopy(approved_changes)
        self.policy = copy.deepcopy(policy)
        for path in (repository, bundles, environments, wheelhouse, receipts):
            preview_release._absolute_directory(path, 'preparation directory')
        roots = (repository, bundles, environments, wheelhouse, receipts)
        require(all(a != b and a not in b.parents and b not in a.parents
                    for i, a in enumerate(roots) for b in roots[i + 1:]),
                'preparation directories must be disjoint')

    def _identity(self, sha):
        bundle._sha(sha)
        policy_digest = hashlib.sha256(json.dumps(
            {'baseline': self.baseline, 'approved_changes': self.changes,
             'environment': self.policy}, sort_keys=True).encode()).hexdigest()
        return {'schema': SCHEMA, 'commit': sha, 'policy_sha256': policy_digest,
                'scope': 'SIG-v1-reporting-only', 'live_money': False,
                'validation': 'NOT_RUN'}

    def prepare(self, sha):
        """Create once; never adopt artifacts lacking a durable completion receipt.

        The caller owns the transaction lock and containment. PREPARING survives
        every failure and requires host reconciliation; retries cannot bless an
        interrupted install or a child whose cleanup was uncertain.
        """
        identity = self._identity(sha)
        receipt = self.receipts / (sha + '.json')
        require(not os.path.lexists(receipt), 'preparation receipt already exists')
        for root in (self.bundles, self.environments):
            require(not os.path.lexists(root / sha), 'unreceipted artifact exists')
        atomic_json(receipt, {**identity, 'status': 'PREPARING'})
        source = bundle.prepare(self.repository, sha, self.bundles,
                                baseline=self.baseline, approved_changes=self.changes)
        target = environment.prepare(source, self.repository, self.environments,
                                     self.wheelhouse, sha=sha, baseline=self.baseline,
                                     approved_changes=self.changes, policy=self.policy,
                                     launcher=self.launcher)
        digest = environment.digest(target / environment.MANIFEST)
        environment.verify(target, source, self.repository, sha=sha,
                           baseline=self.baseline, approved_changes=self.changes,
                           policy=self.policy, expected_manifest_sha256=digest)
        # Bundle creation does not itself fsync: persist it before the receipt.
        for path in [*source.rglob('*'), source, self.bundles]:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        atomic_json(receipt, {**identity, 'status': 'PREPARED',
                             'environment_manifest_sha256': digest,
                             'source_manifest_sha256': environment.digest(source / bundle.MANIFEST)})
        return target

    def verify(self, sha):
        """Reopen trusted receipt after restart; return verified interpreter only."""
        identity = self._identity(sha)
        path = self.receipts / (sha + '.json')
        require(path.is_file() and not path.is_symlink(), 'missing safe preparation receipt')
        require(path.stat().st_size <= 16384, 'oversized preparation receipt')
        try:
            receipt = json.loads(path.read_text())
        except (ValueError, UnicodeError) as exc:
            raise ValueError('invalid preparation receipt') from exc
        require(isinstance(receipt, dict) and receipt.get('live_money') is False,
                'invalid preparation receipt')
        digest = receipt.get('environment_manifest_sha256')
        require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest),
                'missing environment digest')
        source = self.bundles / sha
        require(receipt == {**identity, 'status': 'PREPARED',
                            'environment_manifest_sha256': digest,
                            'source_manifest_sha256': environment.digest(source / bundle.MANIFEST)},
                'preparation receipt mismatch')
        return environment.verify(self.environments / sha, source, self.repository,
                                  sha=sha, baseline=self.baseline,
                                  approved_changes=self.changes, policy=self.policy,
                                  expected_manifest_sha256=digest)
