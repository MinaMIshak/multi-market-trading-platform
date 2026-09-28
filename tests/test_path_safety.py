"""Shared symlink-safety helper reused by main.py, operational.py, egx_scan_history.py."""
from app.path_safety import symlinked


def test_rejects_target_itself_symlinked(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(real, target_is_directory=True)
    assert symlinked(link)


def test_rejects_symlinked_parent(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(real, target_is_directory=True)
    assert symlinked(link / 'child.db')


def test_accepts_plain_path(tmp_path):
    plain = tmp_path / 'plain' / 'child.db'
    plain.parent.mkdir()
    assert not symlinked(plain)
