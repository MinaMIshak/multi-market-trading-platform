"""Artificial series UI fixtures, not market evidence."""
from datetime import timedelta
import json

import pytest

from app import main
from app.paper import shadow_daily_snapshots
from app.paper.shadow_daily_snapshots import append_cash_only_daily_portfolio_snapshot
from tests.test_shadow_daily_snapshots import setup_cash_snapshot
from tests.test_main_shadow_portfolio_ui import prepare as prepare_marked


def prepare(tmp_path, monkeypatch):
    args, portfolio, at = setup_cash_snapshot(tmp_path, monkeypatch)
    first = append_cash_only_daily_portfolio_snapshot(tmp_path, portfolio)
    later = at + timedelta(days=2)
    monkeypatch.setattr(shadow_daily_snapshots, '_now', lambda: later)
    second = append_cash_only_daily_portfolio_snapshot(tmp_path, portfolio)
    (tmp_path / 'input.json').write_text(json.dumps({
        'schema_version': 'shadow-ui-input-v1',
        'watchlist': args[0].model_dump(mode='json'),
        'evidence_packages': [p.model_dump(mode='json') for p in args[1]],
    }))
    doc = {'schema_version': 'shadow-ui-series-v1',
           'portfolio': portfolio.model_dump(mode='json'),
           'snapshots': [{'snapshot_date_utc': day.date().isoformat(), 'requests': []}
                         for day in (at, later)]}
    path = tmp_path / 'series.json'
    path.write_text(json.dumps(doc))
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(tmp_path))
    return path, doc, first, second


def test_series_audits_without_writes_or_filling_gaps(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    series = main.shadow()['series']
    assert len(series['observations']) == 2
    assert series['intervals'][0]['elapsed_days'] == 2
    assert series['performance_status'] == 'VALUATION SERIES ONLY / M7 PERFORMANCE NOT EVALUATED'
    body = main.shadow_page().body.decode()
    assert 'Gross valuation changes are not net trading returns' in body
    assert 'EXPERIMENTAL / PAPER ONLY' in body
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', ['receipt', 'missing', 'duplicate', 'reverse', 'few',
                                   'many', 'policy', 'computed', 'unknown', 'type'])
def test_series_fails_closed_independently(tmp_path, monkeypatch, damage):
    path, doc, first, second = prepare(tmp_path, monkeypatch)
    assert main.shadow()['series'] is not None
    if damage == 'receipt':
        first.write_text('{}')
    elif damage == 'missing':
        second.unlink()
    elif damage == 'duplicate':
        doc['snapshots'][1] = doc['snapshots'][0]
    elif damage == 'reverse':
        doc['snapshots'].reverse()
    elif damage == 'few':
        doc['snapshots'].pop()
    elif damage == 'many':
        doc['snapshots'] *= 257
    elif damage == 'policy':
        doc['portfolio']['initial_capital'] = '999'
    elif damage == 'computed':
        doc['summary'] = {'nav': '999'}
    elif damage == 'unknown':
        doc['snapshots'][0]['nav'] = '999'
    else:
        doc['snapshots'][0]['requests'] = {}
    path.write_text(json.dumps(doc))
    state = main.shadow()
    assert state['available'] is True
    assert state['series'] is None
    assert 'UNAVAILABLE / NO AUDITED VALUATION SERIES' in main.shadow_page().body.decode()


@pytest.mark.parametrize('continuation', [False, True])
def test_marked_requests_are_reaudited_in_series(tmp_path, monkeypatch, continuation):
    _, doc, receipt = prepare_marked(tmp_path, monkeypatch, continuation)
    from app.ui.shadow_input import _decode_mark_requests
    from app.paper.shadow_daily_series import MarkedSnapshotRequest, _audit_request
    from app.paper.shadow_portfolio import ShadowPortfolioPolicy
    from app.ui.shadow_input import _decode
    from datetime import date
    requests = _decode_mark_requests(doc['requests'])
    result = _audit_request(tmp_path, _decode(ShadowPortfolioPolicy, doc['portfolio']),
                            MarkedSnapshotRequest(date.fromisoformat(doc['snapshot_date_utc']), requests))
    assert result == json.loads(receipt.read_bytes())
    receipt.write_text('{}')
    with pytest.raises(ValueError):
        _audit_request(tmp_path, _decode(ShadowPortfolioPolicy, doc['portfolio']),
                       MarkedSnapshotRequest(date.fromisoformat(doc['snapshot_date_utc']), requests))


def test_series_remains_available_without_candidate_transport(tmp_path, monkeypatch):
    _, _, first, _ = prepare(tmp_path, monkeypatch)
    original = main.shadow()['series']
    (tmp_path / 'input.json').unlink()
    state = main.shadow()
    assert not state['available'] and state['collection'] is state['execution'] is None
    assert state['series'] == original
    assert 'VALUATION SERIES ONLY / M7 PERFORMANCE NOT EVALUATED' in main.shadow_page().body.decode()
    first.write_text('{}')
    assert main.shadow()['series'] is None


def test_linked_directory_still_blocks_every_stage(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    (tmp_path / 'input.json').unlink()
    (tmp_path / 'linked').symlink_to(tmp_path, target_is_directory=True)
    state = main.shadow()
    assert state['collection'] is state['execution'] is state['portfolio'] is state['series'] is None
