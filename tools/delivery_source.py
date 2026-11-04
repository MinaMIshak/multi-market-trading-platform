"""Pinned host-side Git transport. Policy comes from the supervisor, never requests.

Only SSH configuration metadata is inspected; private key contents are never read.
The host must protect the policy/configuration/key against concurrent modification.
"""
import hashlib
from pathlib import Path
import shlex
import subprocess

from tools.delivery_runner import GitSource, require


class PinnedGitSource(GitSource):
    def __init__(self, repository, *, origin, ssh_config, config_sha256, identity_file):
        super().__init__(repository)
        self.origin = origin
        self.ssh_config = Path(ssh_config)
        self.config_sha256 = config_sha256
        self.identity_file = str(identity_file)

    def _transport(self):
        path = self.ssh_config
        require(path.is_absolute() and path.resolve() == path and path.is_file(),
                'unsafe SSH configuration path')
        content = path.read_bytes()
        require(hashlib.sha256(content).hexdigest() == self.config_sha256,
                'SSH configuration digest mismatch')
        # ssh -G can execute Match exec, and Include escapes the pinned bytes.
        for line in content.decode('utf-8').splitlines():
            keyword = line.strip().split('=', 1)[0].split()
            require(not keyword or keyword[0].lower() not in ('match', 'include'),
                    'dynamic SSH configuration rejected')
        return ['/usr/bin/ssh', '-F', str(path), '-oBatchMode=yes',
                '-oStrictHostKeyChecking=yes', '-oIdentityAgent=none',
                '-oControlMaster=no', '-oControlPath=none', '-oClearAllForwardings=yes']

    @staticmethod
    def _environment():
        # Exclude inherited Git config, SSH agents, askpass and command overrides.
        return {'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1',
                'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
                'LC_ALL': 'C'}

    def _check_identity(self):
        output = subprocess.check_output(
            [*self._transport(), '-G', 'github-multimarket'],
            env=self._environment(), text=True, timeout=30)
        fields = {}
        for line in output.splitlines():
            key, _, value = line.partition(' ')
            fields.setdefault(key, []).append(value.strip())
        for key, expected in {'hostname': ['github.com'], 'user': ['git'],
                              'identitiesonly': ['yes'],
                              'identityfile': [self.identity_file],
                              'stricthostkeychecking': ['true'],
                              'batchmode': ['yes']}.items():
            require(fields.get(key) == expected, 'SSH deploy-key configuration mismatch')
        for key in ('proxycommand', 'proxyjump', 'localcommand'):
            require(fields.get(key, ['none']) == ['none'], 'SSH command indirection rejected')
        require(fields.get('permitlocalcommand', ['no']) == ['no'],
                'SSH local commands rejected')

    def git(self, *args):
        env = self._environment()
        env['GIT_SSH_COMMAND'] = shlex.join(self._transport())
        env['GIT_SSH_VARIANT'] = 'ssh'
        return subprocess.check_output(
            ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null',
             '-c', 'protocol.allow=never', '-c', 'protocol.ssh.allow=always',
             '-C', str(self.repository), *args], env=env, text=True, timeout=120).strip()

    def check(self, expected):
        super().check(expected)
        require(self.git('remote', 'get-url', '--all', 'origin') == self.origin and
                self.git('remote', 'get-url', '--push', '--all', 'origin') == self.origin,
                'pinned origin mismatch')
        self._check_identity()
