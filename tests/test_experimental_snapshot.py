import io
import json
import subprocess
import tarfile

import pytest

from tools import experimental_snapshot as snapshot


def archive(name='app/example.py', kind=tarfile.REGTYPE):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as out:
        item = tarfile.TarInfo(name)
        item.type = kind
        item.size = 0
        out.addfile(item, io.BytesIO())
    return buffer.getvalue()


@pytest.mark.parametrize('name,kind', [('../escape', tarfile.REGTYPE),
    ('/app/escape', tarfile.REGTYPE), ('app/link', tarfile.SYMTYPE),
    ('app/link', tarfile.LNKTYPE), ('data/platform.db', tarfile.REGTYPE)])
def test_unsafe_archive_rejected_before_write(tmp_path, name, kind):
    with pytest.raises(ValueError):
        snapshot.extract_source(archive(name, kind), tmp_path)
    assert not list(tmp_path.iterdir())


def setup_candidate(tmp_path, monkeypatch, *, dirty=False, failed=False, mutate=False):
    root = tmp_path / 'builds'
    root.mkdir(mode=0o700)
    previous = root / 'last-known-good'
    previous.write_text('preserve me')
    def git(repo, *args):
        if args[0] == 'status':
            return b' M app/example.py' if dirty else b''
        if args[0] == 'rev-parse':
            return b'a' * 40
        return archive()
    def run(command, **kwargs):
        assert kwargs['env']['PYTHONDONTWRITEBYTECODE'] == '1'
        assert 'SECRET_TEST_TOKEN' not in kwargs['env']
        if mutate:
            (kwargs['cwd'] / 'app/example.py').write_text('changed')
        return subprocess.CompletedProcess(command, 1 if failed else 0)
    monkeypatch.setenv('SECRET_TEST_TOKEN', 'artificial')
    monkeypatch.setattr(snapshot, 'git', git)
    monkeypatch.setattr(snapshot.subprocess, 'run', run)
    return root, previous


def test_success_receipt_binds_source_and_preserves_previous(tmp_path, monkeypatch):
    root, previous = setup_candidate(tmp_path, monkeypatch)
    candidate = snapshot.prepare(tmp_path, root)
    receipt = json.loads((candidate / 'candidate.json').read_text())
    assert receipt['commit'] == 'a' * 40
    assert receipt['files'] == snapshot.hashes(candidate / 'source')
    assert receipt['runtime_promoted'] is False
    assert previous.read_text() == 'preserve me'


@pytest.mark.parametrize('failure', ['dirty', 'failed', 'mutate'])
def test_bad_candidate_cannot_replace_previous(tmp_path, monkeypatch, failure):
    root, previous = setup_candidate(tmp_path, monkeypatch, **{failure: True})
    with pytest.raises(ValueError):
        snapshot.prepare(tmp_path, root)
    assert not list(root.rglob('candidate.json'))
    assert previous.read_text() == 'preserve me'


def test_shared_root_rejected(tmp_path, monkeypatch):
    root, _ = setup_candidate(tmp_path, monkeypatch)
    root.chmod(0o755)
    with pytest.raises(ValueError, match='private'):
        snapshot.prepare(tmp_path, root)


def test_head_changed_during_gate_rejected(tmp_path, monkeypatch):
    root, previous = setup_candidate(tmp_path, monkeypatch)
    original = snapshot.git
    reads = 0
    def changed(repo, *args):
        nonlocal reads
        if args[0] == 'rev-parse':
            reads += 1
            return (b'a' if reads == 1 else b'b') * 40
        return original(repo, *args)
    monkeypatch.setattr(snapshot, 'git', changed)
    with pytest.raises(ValueError, match='changed during'):
        snapshot.prepare(tmp_path, root)
    assert not list(root.rglob('candidate.json'))
    assert previous.read_text() == 'preserve me'


def test_verify_committed_candidate(tmp_path, monkeypatch):
    root, _ = setup_candidate(tmp_path, monkeypatch)
    candidate = snapshot.prepare(tmp_path, root)
    assert snapshot.verify(tmp_path, candidate)['commit'] == 'a' * 40


@pytest.mark.parametrize('mutation', ['source', 'receipt_and_source', 'extra',
                                    'missing', 'symlink', 'archive', 'gate'])
def test_verify_rejects_tampering(tmp_path, monkeypatch, mutation):
    root, previous = setup_candidate(tmp_path, monkeypatch)
    candidate = snapshot.prepare(tmp_path, root)
    source = candidate / 'source'
    file = source / 'app/example.py'
    receipt_path = candidate / 'candidate.json'
    receipt = json.loads(receipt_path.read_text())
    if mutation in {'source', 'receipt_and_source'}:
        file.write_text('changed')
        if mutation == 'receipt_and_source':
            receipt['files'] = snapshot.hashes(source)
    elif mutation == 'extra':
        (source / 'app/injected.py').write_text('injected')
    elif mutation == 'missing':
        file.unlink()
    elif mutation == 'symlink':
        file.unlink()
        file.symlink_to(previous)
    elif mutation == 'archive':
        receipt['archive_sha256'] = '0' * 64
    else:
        receipt['test_exit_code'] = False
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        snapshot.verify(tmp_path, candidate)
    assert previous.read_text() == 'preserve me'


def test_verify_rejects_receipt_symlink(tmp_path, monkeypatch):
    root, _ = setup_candidate(tmp_path, monkeypatch)
    candidate = snapshot.prepare(tmp_path, root)
    receipt = candidate / 'candidate.json'
    retained = candidate / 'retained.json'
    receipt.rename(retained)
    receipt.symlink_to(retained)
    with pytest.raises(ValueError, match='receipt'):
        snapshot.verify(tmp_path, candidate)
