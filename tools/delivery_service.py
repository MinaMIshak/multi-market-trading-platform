"""Persistent systemd release binding for protected host wiring, never a CLI.

Units are provisioned separately by the host. This binding cannot install units,
stop a release, or infer filesystem/cgroup isolation from unit settings alone.
"""
import hashlib
from pathlib import Path
import subprocess
import shlex

from tools.delivery_runner import SHA, acceptance, require


class ReleaseService:
    def __init__(self, systemctl, systemctl_sha256, policies, verify_release,
                 verify_isolation, probe):
        self.systemctl = Path(systemctl)
        self.digest = systemctl_sha256
        self.policies = policies
        self.verify_release = verify_release
        self.verify_isolation = verify_isolation
        self.probe = probe

    def _command(self, *args):
        require(self.systemctl.is_absolute() and self.systemctl.resolve() == self.systemctl
                and self.systemctl.is_file()
                and hashlib.sha256(self.systemctl.read_bytes()).hexdigest() == self.digest,
                'service manager executable unverified')
        result = subprocess.run([str(self.systemctl), *args], check=False,
                                capture_output=True, text=True, timeout=30,
                                env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                                stdin=subprocess.DEVNULL)
        require(result.returncode == 0, 'service manager command failed')
        require(len(result.stdout.encode()) <= 65536, 'oversized service observation')
        return result.stdout

    def _policy(self, sha):
        require(isinstance(sha, str) and SHA.fullmatch(sha), 'full SHA required')
        require(self.verify_release(sha) is True, 'release unverified')
        policy = self.policies.get(sha)
        require(isinstance(policy, dict) and set(policy) == {'fragment', 'sha256', 'properties'},
                'service policy missing')
        fragment = Path(policy['fragment'])
        require(fragment.is_absolute() and fragment.resolve() == fragment and fragment.is_file()
                and fragment.name == f'egx-release-{sha}.service'
                and hashlib.sha256(fragment.read_bytes()).hexdigest() == policy['sha256'],
                'service fragment unverified')
        properties = policy['properties']
        require(isinstance(properties, dict) and all(isinstance(k, str) and isinstance(v, str)
                and '\n' not in v for k, v in properties.items()), 'invalid service properties')
        mandatory = dict(LoadState='loaded', NeedDaemonReload='no', DropInPaths='',
                         FragmentPath=str(fragment), KillMode='control-group',
                         SendSIGKILL='yes', NoNewPrivileges='yes', ProtectSystem='strict',
                         ProtectControlGroups='yes', Delegate='no', Type='exec', Restart='on-failure')
        require(all(properties.get(k) == v for k, v in mandatory.items()),
                'required service isolation missing')
        require(all(properties.get(k) for k in ('ExecStart', 'WorkingDirectory', 'User',
                                               'Environment', 'ReadWritePaths'))
                and properties['User'] != 'root', 'service execution policy incomplete')
        try:
            environment = shlex.split(properties['Environment'])
        except ValueError:
            environment = []
        require(environment.count('LIVE_MONEY=false') == 1
                and environment.count(f'EGX_BUILD_REVISION={sha}') == 1
                and sum(v.startswith('LIVE_MONEY=') for v in environment) == 1
                and sum(v.startswith('EGX_BUILD_REVISION=') for v in environment) == 1,
                'service safety environment mismatch')
        require(not {'ActiveState', 'SubState', 'MainPID', 'ControlGroup'} & properties.keys(),
                'runtime state cannot be policy')
        return fragment.name, dict(properties)

    def _observe(self, sha, *, running=False):
        unit, expected = self._policy(sha)
        keys = sorted(set(expected) | {'ActiveState', 'SubState', 'MainPID', 'ControlGroup'})
        require(all(k.isascii() and k.isalnum() for k in keys), 'invalid property name')
        output = self._command('show', '--no-pager', '--property=' + ','.join(keys), unit)
        observed = {}
        for line in output.splitlines():
            key, sep, value = line.partition('=')
            require(sep and key in keys and key not in observed, 'malformed service observation')
            observed[key] = value
        require(set(observed) == set(keys) and all(observed[k] == v for k, v in expected.items()),
                'loaded service policy mismatch')
        if running:
            require(observed['ActiveState'] == 'active' and observed['SubState'] == 'running'
                    and observed['MainPID'].isascii() and observed['MainPID'].isdigit()
                    and int(observed['MainPID']) > 0 and observed['ControlGroup'].startswith('/')
                    and observed['ControlGroup'] != '/', 'persistent service not running')
        # This host verifier must inspect actual ownership, mounts, runtime pins,
        # cgroup restrictions and (when running) PID membership. Unit text is insufficient.
        require(self.verify_isolation(sha, observed, running) is True,
                'host service isolation unverified')
        self._policy(sha)
        return unit

    def contained(self, sha):
        self._observe(sha)
        return True

    def stage(self, sha):
        unit = self._observe(sha)
        self._command('start', unit)  # idempotent; never restart or stop retained releases
        self._observe(sha, running=True)
        result = self.probe(sha)
        # Previous rollback releases may predate SIG-v1; caller gates SIG-v1 for new releases.
        acceptance(result, sha, require_sig=False)
        self._observe(sha, running=True)
        return result
