import hashlib
import subprocess

import pytest

from tools import delivery_bundle as bundle
from tools.delivery_runner import DeliveryError
from tests.test_preview_release import repository, releases_root


def setup(tmp_path):
    repo, base = repository(tmp_path)
    sha = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    policy = {'app/ui/system.py': hashlib.sha256(b'100644\0VALUE = 2\n').hexdigest()}
    return repo, base, sha, policy, releases_root(tmp_path)


def test_exact_source_bundle_includes_tests_and_ignores_dirty_checkout(tmp_path):
    repo, base, sha, policy, root = setup(tmp_path)
    (repo / 'tests/test_x.py').write_text('uncommitted data')
    out = bundle.prepare(repo, sha, root, baseline=base, approved_changes=policy)
    assert (out / 'source/tests/test_x.py').read_bytes() == b''
    result = bundle.verify(out, repo, sha=sha, baseline=base, approved_changes=policy)
    assert result['validation'] == 'NOT_RUN' and result['interpreter'] is None
    assert result['live_money'] is False
    with pytest.raises(DeliveryError, match='already exists'):
        bundle.prepare(repo, sha, root, baseline=base, approved_changes=policy)


@pytest.mark.parametrize('policy', [{}, {'app/ui/system.py': None},
                                  {'app/ui/system.py': '0' * 64},
                                  {'app/ui/*': '0' * 64}])
def test_unapproved_changes_rejected_without_output(tmp_path, policy):
    repo, base, sha, _, root = setup(tmp_path)
    with pytest.raises(DeliveryError, match='approval'):
        bundle.prepare(repo, sha, root, baseline=base, approved_changes=policy)
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('change', ['bytes', 'manifest', 'extra', 'symlink', 'writable', 'missing'])
def test_verification_rejects_tampering(tmp_path, change):
    repo, base, sha, policy, root = setup(tmp_path)
    out = bundle.prepare(repo, sha, root, baseline=base, approved_changes=policy)
    target = out / 'source/tests/test_x.py'
    if change == 'manifest':
        target = out / bundle.MANIFEST
    target.parent.chmod(0o755)
    target.chmod(0o644)
    if change in ('bytes', 'manifest'):
        target.write_text('tampered')
    elif change == 'extra':
        (target.parent / 'extra').write_text('')
        (target.parent / 'extra').chmod(0o444)
    elif change == 'symlink':
        target.unlink()
        target.symlink_to('/tmp')
    elif change == 'missing':
        target.unlink()
    if change != 'writable':
        if target.exists() and not target.is_symlink():
            target.chmod(0o444)
        target.parent.chmod(0o555)
    with pytest.raises(DeliveryError):
        bundle.verify(out, repo, sha=sha, baseline=base, approved_changes=policy)


def test_partial_failure_cleans_up(tmp_path, monkeypatch):
    repo, base, sha, policy, root = setup(tmp_path)
    def fail(*args):
        raise OSError('publish failed')
    monkeypatch.setattr(bundle.os, 'rename', fail)
    with pytest.raises(OSError):
        bundle.prepare(repo, sha, root, baseline=base, approved_changes=policy)
    assert list(root.iterdir()) == []


def commit(repo):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    git('add', '.')
    git('-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-qm', 'fixture')
    return git('rev-parse', 'HEAD')


def test_export_attributes_cannot_hide_tests(tmp_path):
    repo, base, sha, policy, root = setup(tmp_path)
    content = b'tests export-ignore\n'
    (repo / '.gitattributes').write_bytes(content)
    sha = commit(repo)
    policy['.gitattributes'] = hashlib.sha256(b'100644\0' + content).hexdigest()
    out = bundle.prepare(repo, sha, root, baseline=base, approved_changes=policy)
    assert (out / 'source/tests/test_x.py').is_file()


@pytest.mark.parametrize('change', ['mode', 'addition', 'deletion', 'symlink'])
def test_policy_covers_complete_tree(tmp_path, change):
    repo, base, sha, policy, root = setup(tmp_path)
    target = repo / 'tests/test_x.py'
    if change == 'mode':
        target.chmod(0o755)
    elif change == 'addition':
        (repo / 'app/challenger.py').write_text('unapproved')
    elif change == 'deletion':
        target.unlink()
    else:
        target.unlink()
        target.symlink_to('/tmp')
    sha = commit(repo)
    with pytest.raises(DeliveryError):
        bundle.prepare(repo, sha, root, baseline=base, approved_changes=policy)
    assert not list(root.iterdir())
