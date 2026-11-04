"""Historical delivery visibility must never disclose private host evidence."""
import json

import pytest

from app.delivery_status import SOCKET_TESTS, load_delivery_status, project_delivery
from app.ui.system import load_system_state, render_delivery, render_system

HEAD = 'a' * 40
OLD = 'b' * 40
SECRET = 'PRIVATE_SCHEDULE_OR_ERROR'


def acceptance(commit=HEAD):
    return dict(commit=commit, live_money=False, http=True, api=True,
                runtime_compatible=True, sig_v1=True, scheduler_target=commit,
                scheduler_verified=True, secret=SECRET)


def receipt():
    return dict(schema='delivery-state-v1', source_head=HEAD, status='SUCCEEDED',
                started_at='2026-11-04T12:00:00+00:00', live_money=False,
                origin_sync='VERIFIED', deployed_release=HEAD, scheduler_target=HEAD,
                tests=dict(commit=HEAD, full_suite=True, reporting_only=True, runtime_rss=True,
                           socket_tests={key: True for key in SOCKET_TESTS}, private=SECRET),
                candidate_validation=acceptance(), production_validation=acceptance(),
                rollback={'schedule': SECRET}, blocker=SECRET,
                latest_codex_cycle=SECRET, latest_claude_review=SECRET,
                egx_chain_status=SECRET, us_chain_status=SECRET)


def test_success_is_allowlisted_and_historical():
    raw = receipt()
    before = json.dumps(raw)
    result = project_delivery(raw)
    assert json.dumps(raw) == before
    assert result['deployed_release'] == HEAD
    assert result['tests'] == 'PASSED_AT_DELIVERY'
    assert result['scheduler_target'] == HEAD
    assert result['scheduler_health'] is None
    assert result['runtime_verified'] is False
    assert SECRET not in json.dumps(result)
    assert SECRET not in render_delivery({'delivery': result})
    assert 'Historical receipt only' in render_delivery({'delivery': result})


@pytest.mark.parametrize('key,value', [
    ('schema', 'bad'), ('status', []), ('status', 'GREEN'), ('source_head', 'short'),
    ('live_money', True), ('live_money', 0), ('live_money', 'false'),
    ('started_at', None), ('started_at', '2026-11-04T12:00:00'),
])
def test_invalid_receipt_fails_closed(key, value):
    assert project_delivery(receipt() | {key: value})['status'] == 'UNAVAILABLE'


@pytest.mark.parametrize('field', ['http', 'api', 'runtime_compatible', 'sig_v1',
                                  'live_money', 'commit', 'scheduler_target', 'scheduler_verified'])
def test_production_gate_required(field):
    raw = receipt()
    raw['production_validation'].pop(field)
    result = project_delivery(raw)
    assert result['deployed_release'] is None
    assert result['scheduler_target'] is None
    assert result['deployment_result'] == 'UNVERIFIED_TERMINAL_RECEIPT'


@pytest.mark.parametrize('field', ['full_suite', 'reporting_only', 'runtime_rss', 'socket_tests', 'commit'])
def test_host_test_gates_required(field):
    raw = receipt()
    raw['tests'].pop(field)
    assert project_delivery(raw)['deployed_release'] is None


def test_rollback_requires_previous_release_acceptance():
    raw = receipt() | dict(status='ROLLED_BACK', deployed_release=OLD, scheduler_target=OLD)
    assert project_delivery(raw)['deployed_release'] is None
    raw['rollback_validation'] = acceptance(OLD)
    raw['rollback_validation'].pop('sig_v1')
    result = project_delivery(raw)
    assert result['deployed_release'] == OLD
    assert result['blocker'] == 'DELIVERY_FAILED'
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('phase', ['STARTED', 'PREPARING', 'CUTOVER_PENDING', 'RECOVERING',
                                  'RECOVERY_REQUIRED', 'FAILED'])
def test_nonterminal_or_failed_receipt_never_claims_deployment(phase):
    result = project_delivery(receipt() | {'status': phase})
    assert result['deployed_release'] is None
    assert result['scheduler_target'] is None


@pytest.mark.parametrize('payload', [b'{', b'null', b'[]', b'\xff', b'x' * 131073])
def test_unreadable_payload_is_unavailable(tmp_path, monkeypatch, payload):
    path = tmp_path / 'receipt.json'
    path.write_bytes(payload)
    monkeypatch.setenv('EGX_DELIVERY_RECEIPT', str(path))
    assert load_delivery_status()['status'] == 'UNAVAILABLE'


def test_loader_is_explicit_readonly_and_rejects_symlink(tmp_path, monkeypatch):
    monkeypatch.delenv('EGX_DELIVERY_RECEIPT', raising=False)
    assert load_delivery_status()['status'] == 'UNAVAILABLE'
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(receipt()))
    before = path.read_bytes()
    monkeypatch.setenv('EGX_DELIVERY_RECEIPT', str(path))
    assert load_delivery_status()['deployed_release'] == HEAD
    assert path.read_bytes() == before
    link = tmp_path / 'link'
    link.symlink_to(path)
    monkeypatch.setenv('EGX_DELIVERY_RECEIPT', str(link))
    assert load_delivery_status()['status'] == 'UNAVAILABLE'
    monkeypatch.setenv('EGX_DELIVERY_RECEIPT', 'relative.json')
    assert load_delivery_status()['status'] == 'UNAVAILABLE'


def test_api_and_page_share_allowlisted_projection(tmp_path, monkeypatch):
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(receipt()))
    monkeypatch.setenv('EGX_DELIVERY_RECEIPT', str(path))
    state = load_system_state()
    assert state['delivery'] == project_delivery(receipt())
    page = render_system(state)
    assert page.count('<h2>Autonomous delivery</h2>') == 1
    assert SECRET not in json.dumps(state['delivery'])
    assert SECRET not in page


def test_renderer_escapes_values_and_ignores_private_keys():
    html = render_delivery({'delivery': {'source_head': '<script>', 'rollback': SECRET}})
    assert '&lt;script&gt;' in html
    assert '<script>' not in html
    assert SECRET not in html
