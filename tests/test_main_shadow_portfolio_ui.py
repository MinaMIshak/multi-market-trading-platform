"""Artificial native snapshot UI integration, never empirical evidence."""
from dataclasses import fields
import json

import pytest

from app import main
from tests.test_shadow_daily_snapshots import (
    setup_two_position_marked_snapshot, setup_continuation_marked_snapshot,
)


def prepare(tmp_path, monkeypatch, continuation):
    if continuation:
        portfolio, request, at, receipt, _, _ = setup_continuation_marked_snapshot(tmp_path, monkeypatch)
        requests = (request,)
    else:
        portfolio, requests, at, receipt = setup_two_position_marked_snapshot(tmp_path, monkeypatch)

    def encode(value):
        if value is None:
            return None
        if isinstance(value, tuple):
            return [encode(item) for item in value]
        return value.model_dump(mode='json')

    request = requests[0]
    (tmp_path / 'input.json').write_text(json.dumps({
        'schema_version': 'shadow-ui-input-v1',
        'watchlist': encode(request.watchlist),
        'evidence_packages': encode(request.watchlist_packages),
    }))
    document = {
        'schema_version': 'shadow-ui-portfolio-v1',
        'portfolio': encode(portfolio), 'snapshot_date_utc': at.date().isoformat(),
        'requests': [{'kind': 'CONTINUATION' if continuation else 'SAME_SESSION',
                      'inputs': {field.name: encode(getattr(item, field.name)) for field in fields(item)}}
                     for item in requests],
    }
    path = tmp_path / 'portfolio.json'
    path.write_text(json.dumps(document))
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(tmp_path))
    return path, document, receipt


@pytest.mark.parametrize('continuation', [False, True])
def test_portfolio_ui_audits_complete_snapshot_without_writes(tmp_path, monkeypatch, continuation):
    _, _, receipt = prepare(tmp_path, monkeypatch, continuation)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert main.shadow()['portfolio'] == json.loads(receipt.read_bytes())
    body = main.shadow_page().body.decode()
    assert 'Historical native paper portfolio snapshot' in body
    assert 'not current NAV or liquidation value' in body
    assert 'EXPERIMENTAL / PAPER ONLY' in body
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('continuation', [False, True])
@pytest.mark.parametrize('damage', ['missing', 'receipt', 'omit', 'duplicate', 'policy', 'package', 'computed', 'kind'])
def test_portfolio_ui_clears_invalid_snapshot_on_next_read(tmp_path, monkeypatch, continuation, damage):
    path, document, receipt = prepare(tmp_path, monkeypatch, continuation)
    assert main.shadow()['portfolio'] is not None
    if damage == 'missing':
        receipt.unlink()
    elif damage == 'receipt':
        receipt.write_text('{}')
    elif damage == 'omit':
        document['requests'].pop()
    elif damage == 'duplicate':
        document['requests'].append(document['requests'][0])
    elif damage == 'policy':
        document['portfolio']['initial_capital'] = '999'
    elif damage == 'package':
        document['requests'][0]['inputs']['exit_packages'][0]['review']['approved'] = False
    elif damage == 'computed':
        document['gross_marked_nav'] = '999'
    else:
        document['requests'][0]['kind'] = 'UNKNOWN'
    path.write_text(json.dumps(document))
    state = main.shadow()
    assert state['available'] is True
    assert state['portfolio'] is None
    assert 'UNAVAILABLE / NO AUDITED PORTFOLIO SNAPSHOT' in main.shadow_page().body.decode()


def test_cash_only_snapshot_is_accounting_not_performance(tmp_path, monkeypatch):
    from tests.test_shadow_daily_snapshots import setup_cash_snapshot
    from app.paper.shadow_daily_snapshots import append_cash_only_daily_portfolio_snapshot
    args, portfolio, at = setup_cash_snapshot(tmp_path, monkeypatch)
    receipt = append_cash_only_daily_portfolio_snapshot(tmp_path, portfolio)
    (tmp_path / 'input.json').write_text(json.dumps({
        'schema_version': 'shadow-ui-input-v1',
        'watchlist': args[0].model_dump(mode='json'),
        'evidence_packages': [p.model_dump(mode='json') for p in args[1]],
    }))
    (tmp_path / 'portfolio.json').write_text(json.dumps({
        'schema_version': 'shadow-ui-portfolio-v1',
        'portfolio': portfolio.model_dump(mode='json'),
        'snapshot_date_utc': at.date().isoformat(), 'requests': [],
    }))
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(tmp_path))
    view = main.shadow()['portfolio']
    assert view == json.loads(receipt.read_bytes())
    assert view['valuation']['open_position_count'] == 0
    assert view['valuation']['performance']['nav'] is None
    assert 'AUTHENTICATED NATIVE CASH-ONLY DAILY VALUATION' in main.shadow_page().body.decode()


@pytest.mark.parametrize('continuation', [False, True])
@pytest.mark.parametrize('damage', ['absent', 'malformed'])
def test_snapshot_does_not_depend_on_candidate_transport(tmp_path, monkeypatch, continuation, damage):
    _, _, receipt = prepare(tmp_path, monkeypatch, continuation)
    path = tmp_path / 'input.json'
    if damage == 'absent':
        path.unlink()
    else:
        path.write_text('{}')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    state = main.shadow()
    assert not state['available'] and state['collection'] is state['execution'] is None
    assert state['portfolio'] == json.loads(receipt.read_bytes())
    assert 'UNAVAILABLE / NO AUDITED COLLECTION' in main.shadow_page().body.decode()
    assert 'AUTHENTICATED NATIVE DAILY GROSS MARKED VALUATION' in main.shadow_page().body.decode()
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    receipt.write_text('{}')
    assert main.shadow()['portfolio'] is None
