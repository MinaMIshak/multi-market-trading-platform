"""Offline, release-local environments from independently pinned host policy.

No defaults approve a Python build or dependency. Call under the delivery lock;
protect policy, wheelhouse, base runtime and output root from concurrent writers.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

from tools import delivery_bundle, preview_release
from tools.delivery_process import CleanupError
from tools.delivery_runner import require

MANIFEST = 'ENVIRONMENT.json'
SCHEMA = 'delivery-environment-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy(policy):
    require(isinstance(policy, dict) and set(policy) == {
        'python', 'python_sha256', 'wheels', 'inventory'}, 'invalid environment policy')
    python = Path(policy['python'])
    require(python.is_absolute() and python.resolve() == python and python.is_file(),
            'canonical base interpreter required')
    require(digest(python) == policy['python_sha256'], 'base interpreter mismatch')
    require(isinstance(policy['wheels'], dict) and policy['wheels'], 'empty wheel policy')
    for name, sha in policy['wheels'].items():
        require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.+!-]+\.whl', name)
                and isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha),
                'invalid pinned wheel')
    require(isinstance(policy['inventory'], dict) and policy['inventory'],
            'empty inventory policy')
    for name, version in policy['inventory'].items():
        require(isinstance(name, str) and re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', name)
                and isinstance(version, str) and version and '\n' not in version,
                'invalid pinned inventory')
    return python


def _files(root, *, readonly=False):
    result = {}
    for path in [root, *sorted(root.rglob('*'))]:
        require(not path.is_symlink() and (path.is_file() or path.is_dir()),
                'unsafe environment entry')
        if readonly:
            require(not path.stat().st_mode & 0o222, 'writable environment entry')
        if path.is_file() and path != root / MANIFEST:
            result[str(path.relative_to(root))] = {
                'sha256': digest(path), 'executable': bool(path.stat().st_mode & 0o111)}
    return result


INVENTORY = '''import importlib.metadata as m, json, re, sys
rows = list(m.distributions())
names = [re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower() for d in rows]
assert len(names) == len(set(names)), "duplicate distributions"
assert sys.prefix != sys.base_prefix, "virtualenv required"
assert sys.flags.isolated, "isolated interpreter required"
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps(dict(zip(names, [d.version for d in rows])), sort_keys=True))
'''


def prepare(bundle, repository, root, wheelhouse, *, sha, baseline,
            approved_changes, policy, timeout=600, launcher=None):
    """Create once at final path (venv launchers are not relocatable).

    The manifest is the last completion marker. A crash leaves a rejected partial
    directory, never a reusable environment. No package index or source builds.
    """
    require(launcher is not None, 'protected environment launcher required')
    source = delivery_bundle.verify(bundle, repository, sha=sha, baseline=baseline,
                                    approved_changes=approved_changes)
    python = _policy(policy)
    preview_release._absolute_directory(root, 'environment root')
    preview_release._absolute_directory(wheelhouse, 'wheelhouse')
    for name, expected in policy['wheels'].items():
        path = wheelhouse / name
        require(path.is_file() and not path.is_symlink() and digest(path) == expected,
                'wheel hash mismatch')
    target = root / sha
    require(not target.exists() and not target.is_symlink(), 'environment already exists')
    target.mkdir(mode=0o700)
    try:
        # Copy the exact reviewed wheels; pip never sees mutable external extras.
        wheels = target / 'wheels'
        wheels.mkdir()
        for name, expected in policy['wheels'].items():
            shutil.copyfile(wheelhouse / name, wheels / name)
            require(digest(wheels / name) == expected, 'copied wheel mismatch')
        lock = target / 'requirements.lock'
        lock.write_text(''.join(f'{(wheels / name).as_uri()} --hash=sha256:{value}\n'
                                for name, value in sorted(policy['wheels'].items())))
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(target), 'LANG': 'C.UTF-8',
               'LIVE_MONEY': 'false', 'PIP_CONFIG_FILE': os.devnull,
               'PYTHONDONTWRITEBYTECODE': '1'}
        def run(args):
            launcher.run('environment', [str(x) for x in args], cwd=target, timeout=timeout, env=env)
        venv = target / 'venv'
        run([python, '-I', '-m', 'venv', '--copies', venv])
        # CPython creates this internal convenience symlink even with --copies.
        lib64 = venv / 'lib64'
        if lib64.is_symlink():
            require(os.readlink(lib64) == 'lib', 'unexpected venv link')
            lib64.unlink()
        interpreter = venv / 'bin/python'
        run([interpreter, '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check',
             'install', '--no-index', '--no-deps', '--no-cache-dir', '--no-compile',
             '--only-binary=:all:', '--require-hashes', '-r', lock])
        inventory = target / 'inventory.json'
        run([interpreter, '-I', '-B', '-c', INVENTORY, inventory])
        require(json.loads(inventory.read_text()) == policy['inventory'],
                'installed inventory mismatch')
        run([interpreter, '-I', '-m', 'pip', '--isolated', 'check'])
        require(digest(python) == policy['python_sha256'], 'base interpreter changed')
        require(digest(interpreter) == policy['python_sha256'], 'copied interpreter mismatch')
        files = _files(target)
        manifest = {'schema': SCHEMA, 'commit': sha, 'policy': policy,
                    'source_manifest_sha256': digest(bundle / delivery_bundle.MANIFEST),
                    'source_files': source['files'], 'files': files,
                    'interpreter': 'venv/bin/python', 'validation': 'NOT_RUN',
                    'live_money': False}
        (target / MANIFEST).write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        for path in sorted(target.rglob('*'), reverse=True):
            path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
        target.chmod(0o555)
        # Persist bytes and directory entries before the host records the digest.
        for path in [*target.rglob('*'), target, root]:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return target
    except CleanupError:
        # A possibly live child must not race deletion/reuse of this directory.
        raise
    except BaseException:
        shutil.rmtree(target, onerror=preview_release._writable_retry)
        raise


def verify(environment, bundle, repository, *, sha, baseline, approved_changes,
           policy, expected_manifest_sha256):
    """Require supervisor-held manifest digest, not a mutable self-attestation."""
    _policy(policy)
    source = delivery_bundle.verify(bundle, repository, sha=sha, baseline=baseline,
                                    approved_changes=approved_changes)
    preview_release._absolute_directory(environment, 'environment')
    require(environment.name == sha, 'environment identity mismatch')
    files = _files(environment, readonly=True)
    manifest_path = environment / MANIFEST
    require(digest(manifest_path) == expected_manifest_sha256, 'environment manifest mismatch')
    manifest = json.loads(manifest_path.read_text())
    require(manifest == {'schema': SCHEMA, 'commit': sha, 'policy': policy,
                        'source_manifest_sha256': digest(bundle / delivery_bundle.MANIFEST),
                        'source_files': source['files'], 'files': files,
                        'interpreter': 'venv/bin/python', 'validation': 'NOT_RUN',
                        'live_money': False}, 'environment content mismatch')
    return environment / manifest['interpreter']
