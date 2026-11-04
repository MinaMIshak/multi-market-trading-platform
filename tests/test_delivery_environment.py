"""Offline provisioning contract using disposable source/policy fixtures."""
import json
from pathlib import Path
import shutil
import sys

import pytest

from tools import delivery_bundle, delivery_environment as env
from tools.delivery_runner import DeliveryError
from tools.delivery_process import CleanupError
from tests.test_delivery_bundle import setup


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    repo, baseline, sha, changes, root = setup(tmp_path)
    bundle = delivery_bundle.prepare(repo, sha, root, baseline=baseline, approved_changes=changes)
    environments = tmp_path / 'environments'
    environments.mkdir()
    wheels = tmp_path / 'wheelhouse'
    wheels.mkdir()
    wheel = wheels / 'example-1.0-py3-none-any.whl'
    wheel.write_bytes(b'test fixture wheel; never executed')
    python = Path(sys.executable).resolve()
    policy = {'python': str(python), 'python_sha256': env.digest(python),
              'wheels': {wheel.name: env.digest(wheel)},
              'inventory': {'pip': '25.0', 'example': '1.0'}}
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        target = environments / sha
        if 'venv' in argv:
            venv = target / 'venv'
            (venv / 'bin').mkdir(parents=True)
            shutil.copyfile(python, venv / 'bin/python')
            (venv / 'bin/python').chmod(0o755)
            (venv / 'lib').mkdir()
            (venv / 'lib64').symlink_to('lib')
        if env.INVENTORY in argv:
            Path(argv[-1]).write_text(json.dumps(policy['inventory']))
    monkeypatch.setattr(env, 'run_owned', run)
    args = (bundle, repo, environments, wheels)
    kw = dict(sha=sha, baseline=baseline, approved_changes=changes, policy=policy)
    return args, kw, calls


def verify(out, args, kw, digest=None):
    return env.verify(out, args[0], args[1], **kw,
                      expected_manifest_sha256=digest or env.digest(out / env.MANIFEST))


def test_preparation_is_offline_isolated_and_exact(prepared):
    args, kw, calls = prepared
    out = env.prepare(*args, **kw)
    interpreter = verify(out, args, kw)
    assert interpreter == out / 'venv/bin/python'
    assert interpreter.stat().st_mode & 0o111
    assert not (out / 'venv/lib64').exists()
    assert json.loads((out / env.MANIFEST).read_text())['validation'] == 'NOT_RUN'
    assert len(calls) == 4
    for argv, options in calls:
        assert '-I' in argv
        assert options['env']['LIVE_MONEY'] == 'false'
        assert not any(k in options['env'] for k in ('PYTHONPATH', 'PIP_INDEX_URL'))
    install = calls[1][0]
    for flag in ('--no-index', '--no-deps', '--require-hashes', '--only-binary=:all:'):
        assert flag in install
    assert '--hash=sha256:' in (out / 'requirements.lock').read_text()
    with pytest.raises(DeliveryError, match='already exists'):
        env.prepare(*args, **kw)


@pytest.mark.parametrize('fault', ['base', 'wheel', 'symlink', 'traversal', 'inventory'])
def test_untrusted_inputs_fail_before_execution(prepared, fault):
    args, kw, calls = prepared
    policy = kw['policy']
    if fault == 'base':
        policy['python_sha256'] = '0' * 64
    elif fault == 'wheel':
        policy['wheels'][next(iter(policy['wheels']))] = '0' * 64
    elif fault == 'symlink':
        wheel = args[3] / next(iter(policy['wheels']))
        wheel.unlink()
        wheel.symlink_to(Path(policy['python']))
    elif fault == 'traversal':
        policy['wheels'] = {'../escape.whl': '0' * 64}
    else:
        policy['inventory'] = {}
    with pytest.raises(DeliveryError):
        env.prepare(*args, **kw)
    assert not calls and not list(args[2].iterdir())


@pytest.mark.parametrize('stage', [1, 2, 3, 4])
def test_command_failure_cleans_partial_environment(prepared, monkeypatch, stage):
    args, kw, calls = prepared
    original = env.run_owned
    def fail(argv, **options):
        if len(calls) + 1 == stage:
            raise DeliveryError('host command failed')
        original(argv, **options)
    monkeypatch.setattr(env, 'run_owned', fail)
    with pytest.raises(DeliveryError):
        env.prepare(*args, **kw)
    assert not list(args[2].iterdir())


def test_uncertain_cleanup_retains_unusable_partial(prepared, monkeypatch):
    args, kw, _ = prepared
    def fail(*a, **k):
        raise CleanupError('uncertain child')
    monkeypatch.setattr(env, 'run_owned', fail)
    with pytest.raises(CleanupError):
        env.prepare(*args, **kw)
    partial = args[2] / kw['sha']
    assert partial.is_dir() and not (partial / env.MANIFEST).exists()
    with pytest.raises(DeliveryError, match='already exists'):
        env.prepare(*args, **kw)


def test_inventory_mismatch_fails_closed(prepared, monkeypatch):
    args, kw, _ = prepared
    original = env.run_owned
    def wrong(argv, **options):
        original(argv, **options)
        if env.INVENTORY in argv:
            Path(argv[-1]).write_text('{"extra": "1"}')
    monkeypatch.setattr(env, 'run_owned', wrong)
    with pytest.raises(DeliveryError, match='inventory mismatch'):
        env.prepare(*args, **kw)
    assert not list(args[2].iterdir())


@pytest.mark.parametrize('fault', ['bytes', 'executable', 'writable', 'extra', 'link',
                                  'manifest', 'nested_manifest', 'source', 'policy'])
def test_verify_rejects_tampering(prepared, fault):
    args, kw, _ = prepared
    out = env.prepare(*args, **kw)
    pinned = env.digest(out / env.MANIFEST)
    target = out / 'inventory.json'
    if fault == 'bytes':
        target.chmod(0o644)
        target.write_text('{}')
        target.chmod(0o444)
    elif fault == 'executable':
        (out / 'venv/bin/python').chmod(0o444)
    elif fault == 'writable':
        target.chmod(0o644)
    elif fault in ('extra', 'nested_manifest'):
        parent = out if fault == 'extra' else out / 'venv'
        parent.chmod(0o755)
        extra = parent / ('extra' if fault == 'extra' else env.MANIFEST)
        extra.write_text('{}')
        extra.chmod(0o444)
        parent.chmod(0o555)
    elif fault == 'link':
        out.chmod(0o755)
        target.unlink()
        target.symlink_to(args[0] / delivery_bundle.MANIFEST)
        out.chmod(0o555)
    elif fault == 'manifest':
        target = out / env.MANIFEST
        target.chmod(0o644)
        target.write_text('{}')
        target.chmod(0o444)
    elif fault == 'source':
        target = args[0] / 'source/requirements.txt'
        target.chmod(0o644)
    else:
        kw['policy']['inventory']['extra'] = '1'
    with pytest.raises(DeliveryError):
        verify(out, args, kw, pinned)


def test_real_offline_disposable_environment(prepared, monkeypatch):
    """Exercise actual venv/pip with a tiny local wheel, never the dev venv."""
    import ensurepip
    import zipfile
    from tools.delivery_process import run_owned
    args, kw, _ = prepared
    monkeypatch.setattr(env, 'run_owned', run_owned)
    wheel = args[3] / next(iter(kw['policy']['wheels']))
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('example.py', 'VALUE = 1\n')
        archive.writestr('example-1.0.dist-info/METADATA',
                         'Metadata-Version: 2.1\nName: example\nVersion: 1.0\n')
        archive.writestr('example-1.0.dist-info/WHEEL',
                         'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr('example-1.0.dist-info/RECORD', '')
    kw['policy']['wheels'][wheel.name] = env.digest(wheel)
    kw['policy']['inventory'] = {'pip': ensurepip.version(), 'example': '1.0'}
    out = env.prepare(*args, **kw, timeout=60)
    python = verify(out, args, kw)
    run_owned([str(python), '-I', '-B', '-c', 'import example; assert example.VALUE == 1'],
              cwd=out, timeout=5, env={'LIVE_MONEY': 'false'})
    verify(out, args, kw)
