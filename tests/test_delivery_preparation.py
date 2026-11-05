"""Durable host preparation receipts; disposable artifacts, no host deployment."""
import json
import shutil

import pytest

from tests.test_delivery_environment import prepared
from tools import delivery_preparation as preparation, delivery_environment as env
from tools import preview_release
from tools.delivery_runner import DeliveryError
from tools.delivery_process import CleanupError


@pytest.fixture
def host(prepared, tmp_path):
    args, kw, calls = prepared
    shutil.rmtree(args[0], onerror=preview_release._writable_retry)
    receipts = tmp_path / 'receipts'
    receipts.mkdir()
    adapter = preparation.PreparedRelease(args[1], args[0].parent, args[2], args[3],
                                         receipts, baseline=kw['baseline'],
                                         approved_changes=kw['approved_changes'],
                                         policy=kw['policy'], launcher=kw['launcher'])
    return adapter, kw['sha'], calls


def test_receipt_survives_restart_without_repreparing(host):
    adapter, sha, calls = host
    target = adapter.prepare(sha)
    assert adapter.verify(sha) == target / 'venv/bin/python'
    restarted = preparation.PreparedRelease(
        adapter.repository, adapter.bundles, adapter.environments, adapter.wheelhouse,
        adapter.receipts, baseline=adapter.baseline, approved_changes=adapter.changes,
        policy=adapter.policy)
    assert restarted.verify(sha) == target / 'venv/bin/python'
    receipt = json.loads((adapter.receipts / (sha + '.json')).read_text())
    assert receipt['validation'] == 'NOT_RUN' and receipt['live_money'] is False
    with pytest.raises(DeliveryError, match='already exists'):
        restarted.prepare(sha)
    assert len(calls) == 4


@pytest.mark.parametrize('fault', ['status', 'commit', 'policy', 'live', 'digest',
                                  'extra', 'missing', 'symlink', 'malformed', 'numeric_live'])
def test_receipt_tampering_blocks_use(host, fault):
    adapter, sha, _ = host
    adapter.prepare(sha)
    path = adapter.receipts / (sha + '.json')
    value = json.loads(path.read_text())
    if fault == 'missing':
        path.unlink()
    elif fault == 'symlink':
        other = path.with_suffix('.other')
        path.rename(other)
        path.symlink_to(other)
    elif fault == 'malformed':
        path.write_text('{')
    else:
        key, val = {'status': ('status', 'PREPARING'), 'commit': ('commit', '0' * 40),
                    'policy': ('policy_sha256', '0' * 64), 'live': ('live_money', True),
                    'numeric_live': ('live_money', 0),
                    'digest': ('environment_manifest_sha256', '0' * 64),
                    'extra': ('tests', 'PASSED')}[fault]
        value[key] = val
        path.write_text(json.dumps(value))
    with pytest.raises((DeliveryError, ValueError)):
        adapter.verify(sha)


@pytest.mark.parametrize('error', [RuntimeError, CleanupError])
def test_failed_preparation_keeps_durable_blocking_marker(host, monkeypatch, error):
    adapter, sha, _ = host
    def fail(*a, **k):
        raise error('injected failure')
    monkeypatch.setattr(env, 'prepare', fail)
    with pytest.raises(error):
        adapter.prepare(sha)
    receipt = json.loads((adapter.receipts / (sha + '.json')).read_text())
    assert receipt['status'] == 'PREPARING'
    with pytest.raises(DeliveryError):
        adapter.verify(sha)
    with pytest.raises(DeliveryError):
        adapter.prepare(sha)


def test_receipt_publication_crash_does_not_bless_environment(host, monkeypatch):
    adapter, sha, _ = host
    original = preparation.atomic_json
    def write(path, value):
        if value['status'] == 'PREPARED':
            raise OSError('receipt disk failure')
        original(path, value)
    monkeypatch.setattr(preparation, 'atomic_json', write)
    with pytest.raises(OSError):
        adapter.prepare(sha)
    assert (adapter.environments / sha / env.MANIFEST).is_file()
    with pytest.raises(DeliveryError):
        adapter.verify(sha)


def test_rewritten_environment_manifest_cannot_replace_receipt_digest(host):
    adapter, sha, _ = host
    target = adapter.prepare(sha)
    path = target / env.MANIFEST
    path.chmod(0o644)
    path.write_text('{}')
    path.chmod(0o444)
    with pytest.raises(DeliveryError, match='manifest mismatch'):
        adapter.verify(sha)


def test_unreceipted_existing_artifact_rejected(host):
    adapter, sha, calls = host
    (adapter.bundles / sha).mkdir()
    with pytest.raises(DeliveryError, match='unreceipted'):
        adapter.prepare(sha)
    assert not calls and not list(adapter.receipts.iterdir())


def test_changed_trusted_policy_rejected(host):
    adapter, sha, _ = host
    adapter.prepare(sha)
    adapter.policy['inventory']['extra'] = '1'
    with pytest.raises(DeliveryError, match='receipt mismatch'):
        adapter.verify(sha)


def test_overlapping_roots_rejected(host):
    adapter, _, _ = host
    with pytest.raises(DeliveryError, match='disjoint'):
        preparation.PreparedRelease(
            adapter.repository, adapter.bundles, adapter.environments,
            adapter.wheelhouse, adapter.bundles, baseline=adapter.baseline,
            approved_changes=adapter.changes, policy=adapter.policy)


def test_unsafe_identity_rejected_before_writes(host):
    adapter, _, calls = host
    with pytest.raises(DeliveryError, match='full commit'):
        adapter.prepare('../escape')
    assert not calls and not list(adapter.receipts.iterdir())


def test_uncertain_launcher_preserves_preparation(host):
    from types import SimpleNamespace
    adapter, sha, calls = host
    def reject(*args, **kwargs):
        raise CleanupError('admission uncertain')
    adapter.launcher = SimpleNamespace(run=reject)
    with pytest.raises(CleanupError):
        adapter.prepare(sha)
    assert not calls
    assert (adapter.environments / sha).is_dir()
    assert json.loads((adapter.receipts / (sha + '.json')).read_text())['status'] == 'PREPARING'
