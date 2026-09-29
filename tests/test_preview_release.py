import io
import json
import subprocess
import tarfile

import pytest

from tools import preview_release as release


def archive(name='app/example.py', kind=tarfile.REGTYPE):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as out:
        item = tarfile.TarInfo(name)
        item.type = kind
        item.size = 0
        out.addfile(item, io.BytesIO())
    return buffer.getvalue()


def repository(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'app' / 'ui').mkdir(parents=True)
    (repo / 'app' / '__init__.py').write_text('')
    (repo / 'app' / 'ui' / 'system.py').write_text('VALUE = 1\n')
    (repo / 'requirements.txt').write_text('fastapi\n')
    (repo / 'PROGRESS.json').write_text('{"milestone": "M4"}\n')
    (repo / 'tests').mkdir()
    (repo / 'tests' / 'test_x.py').write_text('')
    (repo / 'secret.env').write_text('TOKEN=artificial')

    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
        'commit', '-q', '-m', 'one')
    first = git('rev-parse', 'HEAD')
    (repo / 'app' / 'ui' / 'system.py').write_text('VALUE = 2\n')
    git('-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
        'commit', '-q', '-am', 'two')
    return repo, first


def releases_root(tmp_path):
    root = tmp_path / 'releases'
    root.mkdir(mode=0o700)
    return root


@pytest.mark.parametrize('name,kind', [('../escape', tarfile.REGTYPE),
    ('/app/escape', tarfile.REGTYPE), ('app/link', tarfile.SYMTYPE),
    ('app/link', tarfile.LNKTYPE), ('data/platform.db', tarfile.REGTYPE),
    ('secret.env', tarfile.REGTYPE)])
def test_unsafe_archive_rejected_before_write(tmp_path, name, kind):
    with pytest.raises(ValueError):
        release.extract(archive(name, kind), tmp_path)
    assert not list(tmp_path.iterdir())


def test_export_pins_exact_commit_read_only_with_manifest(tmp_path):
    repo, first = repository(tmp_path)
    root = releases_root(tmp_path)
    # The working tree has moved on; the release must hold the pinned commit.
    out = release.export(repo, first[:12], root)
    assert out == root / first
    assert (out / 'app' / 'ui' / 'system.py').read_text() == 'VALUE = 1\n'
    assert (out / 'PROGRESS.json').is_file()
    assert not (out / 'tests').exists() and not (out / 'secret.env').exists()
    manifest = json.loads((out / release.MANIFEST).read_text())
    assert manifest['commit'] == first and manifest['schema'] == release.SCHEMA
    assert set(manifest['files']) == {'app/__init__.py', 'app/ui/system.py',
                                      'requirements.txt', 'PROGRESS.json'}
    assert all(not p.stat().st_mode & 0o222 for p in [out, *out.rglob('*')])
    assert release.verify(out)['commit'] == first
    assert [p.name for p in root.iterdir()] == [first]


def test_existing_release_is_never_overwritten(tmp_path):
    repo, first = repository(tmp_path)
    root = releases_root(tmp_path)
    release.export(repo, first, root)
    with pytest.raises(FileExistsError):
        release.export(repo, first, root)
    assert [p.name for p in root.iterdir()] == [first]


@pytest.mark.parametrize('commit', ['', 'HEAD', 'main; rm -rf /', '--output=x'])
def test_non_hex_commit_rejected(tmp_path, commit):
    repo, _ = repository(tmp_path)
    with pytest.raises(ValueError):
        release.export(repo, commit, releases_root(tmp_path))


def test_relative_or_missing_root_rejected(tmp_path):
    repo, first = repository(tmp_path)
    with pytest.raises(ValueError):
        release.export(repo, first, tmp_path / 'missing')
    with pytest.raises(ValueError):
        release.export(repo, first, release.Path('relative'))


def test_failed_export_leaves_no_partial_directory(tmp_path, monkeypatch):
    repo, first = repository(tmp_path)
    root = releases_root(tmp_path)
    monkeypatch.setattr(release, 'hashes', lambda directory: {})
    with pytest.raises(ValueError):
        release.export(repo, first, root)
    assert not list(root.iterdir())


def test_verify_detects_tampering(tmp_path):
    repo, first = repository(tmp_path)
    out = release.export(repo, first, releases_root(tmp_path))
    target = out / 'app' / 'ui' / 'system.py'
    target.parent.chmod(0o755)
    target.chmod(0o644)
    target.write_text('VALUE = 99\n')
    with pytest.raises(ValueError, match='differ'):
        release.verify(out)


@pytest.mark.parametrize('change', ['inject', 'delete'])
def test_verify_detects_added_or_removed_files(tmp_path, change):
    repo, first = repository(tmp_path)
    out = release.export(repo, first, releases_root(tmp_path))
    (out / 'app').chmod(0o755)
    if change == 'inject':
        (out / 'app' / 'extra.py').write_text('import os\n')
    else:
        (out / 'app' / '__init__.py').unlink()
    with pytest.raises(ValueError, match='differ'):
        release.verify(out)


def test_failed_export_after_read_only_is_fully_removed(tmp_path, monkeypatch):
    repo, first = repository(tmp_path)
    root = releases_root(tmp_path)
    def fail(*args):
        raise OSError('rename failed')
    monkeypatch.setattr(release.os, 'rename', fail)
    with pytest.raises(OSError):
        release.export(repo, first, root)
    assert not list(root.iterdir())


def test_verify_rejects_manifest_for_other_commit(tmp_path):
    repo, first = repository(tmp_path)
    out = release.export(repo, first, releases_root(tmp_path))
    renamed = out.parent / ('0' * 40)
    out.parent.chmod(0o700)
    out.rename(renamed)
    with pytest.raises(ValueError, match='invalid release manifest'):
        release.verify(renamed)


def test_cli_verify(tmp_path, capsys):
    repo, first = repository(tmp_path)
    root = releases_root(tmp_path)
    assert release.main(['export', '--repo', str(repo), '--commit', first,
                         '--out-root', str(root)]) == 0
    assert release.main(['verify', str(root / first)]) == 0
    assert f'VERIFIED {first} files=4' in capsys.readouterr().out
