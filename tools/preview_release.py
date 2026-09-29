"""Export an immutable, hash-verified source release for a preview runtime.

A preview served from a live Git checkout with ``--reload`` changes whenever
that checkout changes. A release pins the served code to one commit:

    python tools/preview_release.py export --repo /abs/repo --commit <sha> \
        --out-root /abs/releases
    python tools/preview_release.py verify /abs/releases/<sha>

The release holds only what ``app.main`` needs at runtime (``app/``,
``requirements.txt`` and the ``PROGRESS.json`` checkpoint shown by SYSTEM).
It is built in a private temporary directory, made read-only, and renamed into
place atomically; an existing release is never overwritten. It launches
nothing and reads no runtime state.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


SCHEMA = 'preview-release-v1'
MANIFEST = 'RELEASE.json'
CONTENT = ('app', 'requirements.txt', 'PROGRESS.json')


def git(repository, *args):
    return subprocess.check_output(['git', '-C', str(repository), *args])


def _absolute_directory(path, label):
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError(f'absolute non-symlink {label} required')
    if not path.is_dir():
        raise ValueError(f'{label} must be an existing directory')


def extract(archive, destination):
    """Extract regular files/directories only, validating every path first."""
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        members = source.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if (not path.parts or path.is_absolute() or '..' in path.parts
                    or not (member.isfile() or member.isdir())
                    or path.parts[0] not in CONTENT):
                raise ValueError(f'unsafe release archive member: {member.name}')
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output:
                    output.write(source.extractfile(member).read())


def hashes(directory):
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f'unsafe release entry: {path}')
        relative = str(path.relative_to(directory))
        if path.is_file() and relative != MANIFEST:
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _read_only(directory):
    for path in sorted(directory.rglob('*'), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    directory.chmod(0o555)


def _writable_retry(function, path, _):
    os.chmod(os.path.dirname(path), 0o700)
    if os.path.isdir(path):
        os.chmod(path, 0o700)
    function(path)


def export(repository: Path, commit: str, out_root: Path) -> Path:
    repository = repository.resolve(strict=True)
    _absolute_directory(out_root, 'release root')
    if re.fullmatch('[0-9a-f]{7,40}', commit or '') is None:
        raise ValueError('commit must be a hexadecimal Git object name')
    full = git(repository, 'rev-parse', '--verify', commit + '^{commit}').decode().strip()
    release = out_root / full
    if release.exists() or release.is_symlink():
        raise FileExistsError(f'release already exists: {release}')
    archive = git(repository, 'archive', '--format=tar', full, '--', *CONTENT)
    staging = Path(tempfile.mkdtemp(prefix='.partial-', dir=out_root))
    try:
        extract(archive, staging)
        files = hashes(staging)
        if not any(name.startswith('app/') for name in files):
            raise ValueError('release has no app/ source')
        manifest = {'schema': SCHEMA, 'commit': full,
                    'archive_sha256': hashlib.sha256(archive).hexdigest(),
                    'content': list(CONTENT), 'files': files}
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        _read_only(staging)
        os.rename(staging, release)
    except BaseException:
        if sys.version_info >= (3, 12):
            shutil.rmtree(staging, onexc=_writable_retry)
        else:
            shutil.rmtree(staging, onerror=_writable_retry)
        raise
    return release


def verify(release: Path) -> dict:
    """Re-hash a release against its manifest; raise on any difference."""
    _absolute_directory(release, 'release')
    manifest_path = release / MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('missing or unsafe release manifest')
    manifest = json.loads(manifest_path.read_text())
    commit = manifest.get('commit')
    if (manifest.get('schema') != SCHEMA or not isinstance(commit, str)
            or re.fullmatch('[0-9a-f]{40}', commit) is None
            or release.name != commit):
        raise ValueError('invalid release manifest')
    if hashes(release) != manifest.get('files'):
        raise ValueError('release files differ from manifest')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    make = commands.add_parser('export')
    make.add_argument('--repo', type=Path, required=True)
    make.add_argument('--commit', required=True)
    make.add_argument('--out-root', type=Path, required=True)
    check = commands.add_parser('verify')
    check.add_argument('release', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'export':
        release = export(args.repo, args.commit, args.out_root)
        print(release)
    else:
        manifest = verify(args.release)
        print(f"VERIFIED {manifest['commit']} files={len(manifest['files'])}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
