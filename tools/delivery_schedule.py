"""Dedicated host cron.d file binding, used only under the delivery lease.

Policy and release verification are host-owned, never request-supplied. This
module does not install itself or inspect any default production path. The host
must protect the file and parent from other writers and provision cron.d loading.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import tempfile

from tools.delivery_runner import SHA, require


@dataclass(frozen=True)
class CronJob:
    minute: int
    hour: int
    module: str | None = None
    weekdays: tuple[int, ...] = ()  # empty means daily; Sunday=0
    script: str | None = None
    arguments: tuple[str, ...] = ()


class ReleaseSchedule:
    def __init__(self, target, environments, bundles, user, jobs, verify_release,
                 configuration=()):
        self.target = Path(target)
        self.environments, self.bundles = Path(environments), Path(bundles)
        self.user, self.jobs, self.verify_release = user, tuple(jobs), verify_release
        self.configuration = tuple(configuration)
        allowed = {'EGX_STATE', 'EGX_OPERATIONAL_DB', 'EGX_OPERATIONAL_DATA', 'EGX_US_DATA'}
        require(all(isinstance(item, tuple) and len(item) == 2
                    and item[0] in allowed and isinstance(item[1], str)
                    and re.fullmatch(r'/[A-Za-z0-9_./-]+', item[1])
                    and '..' not in Path(item[1]).parts for item in self.configuration),
                'invalid schedule configuration')
        require(len({item[0] for item in self.configuration}) == len(self.configuration),
                'duplicate schedule configuration')
        for path in (self.target.parent, self.environments, self.bundles):
            require(path.is_absolute() and path.resolve() == path and path.is_dir(),
                    'unsafe schedule root')
            require(re.fullmatch(r'/[A-Za-z0-9_./-]+', str(path)), 'unsafe cron path')
        require(re.fullmatch(r'[A-Za-z0-9_-]+', self.target.name), 'invalid cron filename')
        require(re.fullmatch(r'[a-z_][a-z0-9_-]*', user), 'invalid cron user')
        require(self.jobs and all(isinstance(job, CronJob) for job in self.jobs),
                'invalid cron jobs')
        for job in self.jobs:
            require(type(job.minute) is int and 0 <= job.minute <= 59
                    and type(job.hour) is int and 0 <= job.hour <= 23
                    and isinstance(job.weekdays, tuple)
                    and all(type(day) is int and 0 <= day <= 6 for day in job.weekdays)
                    and tuple(sorted(set(job.weekdays))) == job.weekdays
                    and isinstance(job.arguments, tuple)
                    and all(isinstance(arg, str) and all(32 <= ord(c) < 127 for c in arg)
                            and '%' not in arg for arg in job.arguments), 'invalid cron job')
            require((isinstance(job.module, str) and job.script is None
                     and re.fullmatch(r'[a-zA-Z_]\w*(\.[a-zA-Z_]\w*)*', job.module))
                    or (job.module is None and isinstance(job.script, str)
                        and re.fullmatch(r'tools/[A-Za-z0-9_/-]+\.sh', job.script)
                        and '..' not in Path(job.script).parts), 'invalid cron entrypoint')
        require(len(set(self.jobs)) == len(self.jobs), 'duplicate cron jobs')

    def release_schedule(self, sha):
        require(isinstance(sha, str) and SHA.fullmatch(sha), 'full SHA required')
        require(self.verify_release(sha) is True, 'release environment unverified')
        python = self.environments / sha / 'venv/bin/python'
        source = self.bundles / sha / 'source'
        require(python.is_file() and os.access(python, os.X_OK) and source.is_dir()
                and source.resolve() == source, 'release-local runtime missing')
        # No inherited Python path, user shell startup, worktree interpreter or
        # request command. Trusted modules read their host-provisioned configuration.
        lines = ['# delivery-release ' + sha, 'SHELL=/bin/sh',
                 'PATH=/usr/bin:/bin', 'LIVE_MONEY=false', 'PYTHONNOUSERSITE=1']
        environment = ['PATH=/usr/bin:/bin', 'LIVE_MONEY=false', 'PYTHONNOUSERSITE=1',
                       f'EGX_PYTHON={python}', f'EGX_BUILD_REVISION={sha}']
        environment.extend(f'{key}={value}' for key, value in self.configuration)
        for job in self.jobs:
            days = ','.join(map(str, job.weekdays)) if job.weekdays else '*'
            if job.script is not None:
                script = source / job.script
                require(script.is_file() and script.resolve() == script,
                        'release-local script missing or unsafe')
                command = ['/bin/sh', str(script)]
            else:
                command = [str(python), '-m', job.module]
            command.extend(job.arguments)
            lines.append(f'{job.minute} {job.hour} * * {days} {self.user} cd {source} && '
                         + shlex.join(['/usr/bin/env', '-i', *environment, *command]))
        return '\n'.join(lines) + '\n'

    def verify_schedule(self, sha, text):
        return isinstance(text, str) and text == self.release_schedule(sha)

    def read_schedule(self):
        # Missing state is not an empty schedule. Bootstrap is a host action.
        fd = os.open(self.target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            import stat
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_size <= 1048576
                    and info.st_mode & 0o022 == 0, 'unsafe schedule file')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                data = stream.read(1048577)
            require(len(data) <= 1048576, 'oversized schedule')
            return data.decode('utf-8')
        finally:
            os.close(fd)

    def install_schedule(self, text):
        require(isinstance(text, str) and len(text.encode()) <= 1048576,
                'invalid schedule text')
        match = re.match(r'# delivery-release ([0-9a-f]{40})\n', text)
        require(match is not None and self.verify_schedule(match[1], text),
                'unapproved schedule')
        self.read_schedule()  # refuse missing/unsafe bootstrap state
        fd, name = tempfile.mkstemp(prefix='.delivery-', dir=self.target.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(text.encode())
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.fsync(stream.fileno())
            os.replace(name, self.target)
            directory = os.open(self.target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            require(self.read_schedule() == text, 'schedule readback mismatch')
        finally:
            if os.path.exists(name):
                os.unlink(name)
