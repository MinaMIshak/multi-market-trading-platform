"""Immutable exact-source validation bundles; trusted policy, no host mutations.

The supervisor supplies reviewed SHA-256 changes from a verified baseline.
Approval of paths alone is insufficient. This module neither provisions an
interpreter nor claims tests, reporting semantics or runtime acceptance passed.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from tools import preview_release
from tools.delivery_runner import require

MANIFEST = 'BUNDLE.json'
SCHEMA = 'delivery-source-bundle-v1'


def _sha(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value),
            'full commit SHA required')


def _tree(repository, sha):
    _sha(sha)
    entries = preview_release.git(repository, 'ls-tree', '-rz', '--full-tree', sha)
    files = {}
    for entry in entries.split(b'\0'):
        if not entry:
            continue
        metadata, raw_name = entry.split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        name = raw_name.decode('utf-8')
        path = PurePosixPath(name)
        require(kind == 'blob' and mode in ('100644', '100755')
                and path.parts and not path.is_absolute() and '..' not in path.parts
                and str(path) == name, 'unsafe source tree')
        files[name] = (mode, preview_release.git(repository, 'cat-file', 'blob', oid))
    require(files, 'empty source tree')
    return files


def _hashes(files):
    return {name: hashlib.sha256(mode.encode() + b'\0' + value).hexdigest()
            for name, (mode, value) in files.items()}


def approved_source(repository, sha, baseline, approved_changes):
    """Reject *any* unreviewed addition, deletion or changed bytes, including tests."""
    current = _tree(repository, sha)
    previous = _hashes(_tree(repository, baseline))
    hashes = _hashes(current)
    changes = {name: hashes.get(name) for name in previous.keys() | hashes.keys()
               if previous.get(name) != hashes.get(name)}
    require(isinstance(approved_changes, dict) and changes == approved_changes,
            'source differs from reporting-only approval')
    return current


def prepare(repository: Path, sha: str, root: Path, *, baseline: str,
            approved_changes: dict) -> Path:
    """Publish once under the delivery lock; never filter/cherry-pick candidate code."""
    preview_release._absolute_directory(root, 'bundle root')
    files = approved_source(repository, sha, baseline, approved_changes)
    require('requirements.txt' in files and any(p.startswith('tests/') for p in files)
            and any(p.startswith('app/') for p in files), 'incomplete validation source')
    target = root / sha
    require(not target.exists() and not target.is_symlink(), 'bundle already exists')
    staging = Path(tempfile.mkdtemp(prefix='.partial-', dir=root))
    try:
        for name, (mode, content) in files.items():
            path = staging / 'source' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        manifest = {'schema': SCHEMA, 'commit': sha, 'baseline': baseline,
                    'scope': 'SIG-v1-reporting-only', 'live_money': False,
                    'approved_changes': approved_changes, 'files': _hashes(files),
                    'interpreter': None, 'validation': 'NOT_RUN'}
        (staging / MANIFEST).write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        preview_release._read_only(staging)
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, onerror=preview_release._writable_retry)
        raise
    return target


def verify(bundle: Path, repository: Path, *, sha: str, baseline: str,
           approved_changes: dict) -> dict:
    """Bind content to trusted Git/policy again, not a self-asserted local manifest."""
    preview_release._absolute_directory(bundle, 'bundle')
    require(bundle.name == sha, 'bundle identity mismatch')
    files = approved_source(repository, sha, baseline, approved_changes)
    expected = {'schema': SCHEMA, 'commit': sha, 'baseline': baseline,
                'scope': 'SIG-v1-reporting-only', 'live_money': False,
                'approved_changes': approved_changes, 'files': _hashes(files),
                'interpreter': None, 'validation': 'NOT_RUN'}
    actual = {}
    for path in [bundle, *bundle.rglob('*')]:
        require(not path.is_symlink() and (path.is_file() or path.is_dir()),
                'unsafe bundle entry')
        require(not path.stat().st_mode & 0o222, 'writable bundle entry')
        if path.is_file():
            actual[str(path.relative_to(bundle))] = path.read_bytes()
    manifest = actual.pop(MANIFEST, b'')
    require(manifest == (json.dumps(expected, sort_keys=True, indent=2) + '\n').encode(),
            'bundle manifest mismatch')
    require(actual == {'source/' + name: content for name, (_, content) in files.items()},
            'bundle source mismatch')
    return expected
