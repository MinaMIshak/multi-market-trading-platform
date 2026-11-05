"""Artificial verifier fixtures only; no market coverage or execution evidence."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.egx_scan import ScanBlocked
from app.scanner_v2 import MAX_SCOPE, scan_egx


def run(symbols=('A',), sources=None, **kwargs):
    return scan_egx(symbols=symbols, sources=sources or {}, database=None,
                    data_root='/unused', scope_reference='synthetic-selection', **kwargs)


def verified(symbol, status):
    return dict(operation='VERIFIED_SIGNAL_NOT_PUBLISHED', market='EGX', symbol=symbol,
                mode='SHADOW', live='DISABLED', market_data='FRESH', signal_status=status)


def test_mixed_coverage_preserves_gates_and_never_promotes_watch():
    sources = {s: SimpleNamespace(symbol=s) for s in 'ABCDEF'}
    results = [verified('A', 'WATCH'), verified('B', 'READY_NO_SIGNAL'),
               ScanBlocked('private stale details', 'DATA_STALE'),
               ScanBlocked('private history details', 'DATA_INSUFFICIENT'),
               ScanBlocked('private evidence details'), RuntimeError('private exception')]
    with patch('app.egx_scan._verify', side_effect=results):
        report = run(tuple('GFEDCBA'), sources)
    assert report['requested'] == report['selection_size'] == report['excluded'] == 7
    assert report['scanned'] == report['usable_data'] == 2
    assert report['authoritative_universe_size'] is None
    assert report['status_counts'] == dict(WATCH=1, READY_NO_SIGNAL=1,
                                          EVIDENCE_BLOCKED=3, DATA_STALE=1, NOT_READY=1)
    assert sum(report['exclusion_counts'].values()) == 7
    assert [r['symbol'] for r in report['symbols']] == list('ABCDEFG')
    assert report['symbols'][0]['reason'] == 'SETUP_NOT_CONFIRMED'
    assert report['candidates'] == [] and report['candidate_count'] == 0
    assert report['live_money'] is report['execution_allowed'] is False
    assert report['promotion'] == 'NOT_AUTHORIZED'
    assert 'private' not in json.dumps(report)
    assert json.loads(json.dumps(report)) == report


def test_missing_data_deterministic_and_baseline_capacity():
    symbols = [f'FIXTURE{i:03d}' for i in range(224)]
    with patch('app.egx_scan._verify') as verify:
        first = run(symbols)
        second = run(list(reversed(symbols)))
    verify.assert_not_called()
    assert first == second
    assert first['requested'] == first['excluded'] == 224
    assert first['scanned'] == first['usable_data'] == 0
    assert first['exclusion_counts']['EVIDENCE_BLOCKED'] == 224


@pytest.mark.parametrize('symbols', [(), ('A', 'A'), ('a',), (' A',), 'A',
                                    ('A/B',), tuple(f'A{i}' for i in range(MAX_SCOPE+1))])
def test_invalid_scope_rejected_before_verification(symbols):
    with patch('app.scanner_v2.scan_egx_scope') as verify, pytest.raises(ValueError):
        run(symbols)
    verify.assert_not_called()


@pytest.mark.parametrize('changes', [dict(market='US'), dict(live='ENABLED'),
                                      dict(mode='LIVE'), dict(symbol='OTHER'),
                                      dict(market_data='STALE'), dict(signal_status='READY')])
def test_unsafe_or_cross_market_verification_excluded(changes):
    with patch('app.egx_scan._verify', return_value=verified('A', 'WATCH') | changes):
        report = run(sources={'A': SimpleNamespace(symbol='A')})
    assert report['scanned'] == report['candidate_count'] == 0
    assert report['symbols'][0]['reason'] == 'EVIDENCE_BLOCKED'


def test_out_of_scope_inputs_rejected_and_source_errors_preserved():
    with pytest.raises(ValueError):
        run(sources={'B': SimpleNamespace(symbol='B')})
    with pytest.raises(ValueError):
        run(source_errors={'B': 'INVALID_LAUNCH_EVIDENCE'})
    with patch('app.egx_scan._verify') as verify:
        report = run(sources={'A': SimpleNamespace(symbol='A')},
                     source_errors={'A': 'INVALID_LAUNCH_EVIDENCE'})
    verify.assert_not_called()
    assert report['scanned'] == 0


@pytest.mark.parametrize('changes', [dict(requested=2), dict(requested=True), dict(scanned=False), dict(scanned=1),
    dict(status_counts={}), dict(symbols=[]), dict(live_money=True), dict(market='US'),
    dict(scope_reference='OTHER'), dict(scope_kind='AUTHORITATIVE_UNIVERSE')])
def test_coordinator_drift_rejected(changes):
    from app.egx_scan import scan_egx_scope
    original = scan_egx_scope(symbols=('A',), sources={}, database=None,
                              data_root='/unused', scope_reference='synthetic-selection')
    with patch('app.scanner_v2.scan_egx_scope', return_value=original | changes):
        with pytest.raises(ValueError):
            run()


@pytest.mark.parametrize('changes', [dict(symbol='B'), dict(market='US'),
                                    dict(status='WATCH'), dict(scanned=0),
                                    dict(status=[]), dict(scanned=True)])
def test_row_drift_never_creates_partial_report(changes):
    from app.egx_scan import scan_egx_scope
    original = scan_egx_scope(symbols=('A',), sources={}, database=None,
                              data_root='/unused', scope_reference='synthetic-selection')
    original['symbols'][0].update(changes)
    with patch('app.scanner_v2.scan_egx_scope', return_value=original):
        with pytest.raises(ValueError):
            run()
