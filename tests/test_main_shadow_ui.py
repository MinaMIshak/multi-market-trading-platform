"""Existing application integration; isolated artificial evidence only."""
import json

import pytest

from app import main
from tests.test_experimental_ui import disk_shadow, endpoint


@pytest.mark.parametrize('configuration', [None, 'relative', 'missing'])
def test_missing_configuration_fails_closed(tmp_path, monkeypatch, configuration):
    monkeypatch.delenv('EGX_SHADOW_DIRECTORY', raising=False)
    if configuration:
        value = str(tmp_path / 'absent') if configuration == 'missing' else configuration
        monkeypatch.setenv('EGX_SHADOW_DIRECTORY', value)
    state = endpoint(main.app, '/api/shadow')()
    assert not state['available'] and state['collection'] is None
    assert state['mode'] == 'EXPERIMENTAL / PAPER ONLY'
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('damage', ['ledger', 'input', 'package', 'parent_link'])
def test_main_routes_reaudit_disk_input(tmp_path, monkeypatch, damage):
    _, path, document, ledger = disk_shadow(tmp_path, monkeypatch)
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(path.parent))
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    state = main.shadow()
    assert state['available']
    assert state['collection']['candidate_count'] == len(document['watchlist']['candidates'])
    assert state['collection']['performance']['nav'] is None
    assert state['freshness'] == 'NOT ESTABLISHED / FROZEN RECORD ONLY'
    body = main.shadow_page().body.decode()
    assert 'EXPERIMENTAL / PAPER ONLY' in body
    assert 'Freshness NOT ESTABLISHED' in body
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    if damage == 'ledger':
        ledger.unlink()
    elif damage == 'input':
        path.write_text('{}')
    elif damage == 'package':
        document['evidence_packages'][0]['review']['approved'] = False
        path.write_text(json.dumps(document))
    else:
        link = tmp_path / 'linked'
        link.symlink_to(tmp_path, target_is_directory=True)
        monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(link / 'shadow'))
    state = main.shadow()
    assert not state['available'] and state['collection'] is None
    assert str(tmp_path) not in json.dumps(state)


def test_existing_navigation_and_read_only_routes(tmp_path, monkeypatch):
    monkeypatch.setenv('EGX_DB_PATH', str(tmp_path / 'missing.db'))
    body = main.root().body.decode()
    assert 'href="/shadow"' in body
    assert 'href="/performance"' in body
    for path in ('/shadow', '/api/shadow'):
        route = next(r for r in main.app.routes if r.path == path)
        assert route.methods == {'GET'}
    assert not main.app.router.on_startup
