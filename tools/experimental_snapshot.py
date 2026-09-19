"""Prepare a tested committed experimental source snapshot; never launch services."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile


PYTHON = '/home/egx-agent/work/egx-trading-platform/.venv/bin/python'
GATE = ['tests/test_experimental_ui.py', 'tests/test_today_ui.py',
        'tests/test_m7_performance_ui.py']


def git(repository, *args):
    return subprocess.check_output(['git', '-C', str(repository), *args])


def extract_source(archive, destination):
    """Only regular source files/directories; validate all paths before writing."""
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        members = source.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if (not path.parts or path.is_absolute() or '..' in path.parts
                    or not (member.isfile() or member.isdir())
                    or path.parts[0] not in {'app', 'tests', 'requirements.txt'}):
                raise ValueError('unsafe source archive')
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output:
                    output.write(source.extractfile(member).read())


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file()}


def prepare(repository: Path, output_root: Path) -> Path:
    repository = repository.resolve(strict=True)
    if git(repository, 'status', '--porcelain', '--untracked-files=all').strip():
        raise ValueError('committed clean source required')
    commit = git(repository, 'rev-parse', 'HEAD').decode().strip()
    archive = git(repository, 'archive', '--format=tar', commit, '--',
                  'app', 'tests', 'requirements.txt')
    if (not output_root.is_absolute() or output_root.is_symlink()
            or output_root.resolve() != output_root):
        raise ValueError('absolute non-symlink snapshot root required')
    info = output_root.stat()
    if (not output_root.is_dir() or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise ValueError('snapshot root must be owned and private (0700)')
    # A unique candidate never overwrites a previous candidate or running build.
    candidate = Path(tempfile.mkdtemp(prefix=commit + '-', dir=output_root))
    source = candidate / 'source'
    source.mkdir()
    extract_source(archive, source)
    before = hashes(source)
    environment = {'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1',
                   'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
                   'TMPDIR': str(candidate)}
    with (candidate / 'quality.log').open('wb') as log:
        result = subprocess.run(
            [PYTHON, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *GATE],
            cwd=source, env=environment, stdout=log, stderr=subprocess.STDOUT,
            timeout=180,
        )
    if result.returncode or hashes(source) != before:
        raise ValueError('candidate failed quality/source-integrity gate')
    if (git(repository, 'rev-parse', 'HEAD').decode().strip() != commit
            or git(repository, 'status', '--porcelain', '--untracked-files=all').strip()):
        raise ValueError('source changed during preparation')
    receipt = {'schema': 'experimental-source-candidate-v1', 'commit': commit,
               'archive_sha256': hashlib.sha256(archive).hexdigest(),
               'files': before, 'tests': GATE, 'test_exit_code': result.returncode,
               'python': PYTHON, 'mode': 'EXPERIMENTAL / PAPER ONLY',
               'runtime_promoted': False}
    temporary = candidate / 'candidate.tmp'
    temporary.write_text(json.dumps(receipt, indent=2) + '\n')
    temporary.replace(candidate / 'candidate.json')
    return candidate


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    parser.add_argument('output_root', type=Path)
    args = parser.parse_args()
    print(prepare(args.repository, args.output_root))
