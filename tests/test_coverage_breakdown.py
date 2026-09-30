from app.ui.coverage import LEVELS, coverage_breakdown, render_coverage, unknown_breakdown
from app.ui.product import product_state, render_product

MASTER = {'total_instruments': 319, 'by_type': {'EQUITY': 312, 'INDEX': 7},
          'source_providers': ['egid'], 'latest_snapshot_updated_at': None,
          'latest_source_market_date': None}


def row(symbol, provider, status='EVIDENCE_BLOCKED', freshness='STALE'):
    return {'canonical_symbol': symbol, 'provider': provider, 'source_status': status,
            'freshness': freshness, 'source_snapshot_date': '2026-09-24',
            'oldest_market_date': '2025-01-27', 'newest_market_date': '2026-09-24',
            'valid_bar_count': 400, 'quarantined_bar_count': 0}


def counts(levels):
    return {item['level']: item['count'] for item in levels}


UNSCANNED = {'status': 'UNKNOWN', 'run': None}


def test_current_egx_state_separates_every_level():
    levels = counts(coverage_breakdown(
        security_master=MASTER, scan_runs=UNSCANNED,
        daily_observations=[row('COMI', 'tradingview_tvdatafeed'),
                            row('COMI', 'tradingview_tvdatafeed_egx')]))
    assert levels == {'identities': 319, 'equities': 312, 'authoritative_universe': None,
                      'observed_symbols': 1, 'admitted_symbols': 0, 'current_symbols': 0,
                      'admitted_current_symbols': 0, 'scanned_symbols': None,
                      'candidates': None}


def test_identities_never_become_the_universe():
    for master in (MASTER, None):
        levels = counts(coverage_breakdown(security_master=master, daily_observations=[],
                                           scan_runs=UNSCANNED))
        assert levels['authoritative_universe'] is None


def test_current_unadmitted_data_is_not_admitted_current():
    levels = counts(coverage_breakdown(
        security_master=MASTER, scan_runs=UNSCANNED,
        daily_observations=[row('COMI', 'a', freshness='CURRENT'),
                            row('EAST', 'b', status='ADMITTED', freshness='STALE'),
                            row('FWRY', 'c', status='ADMITTED', freshness='CURRENT'),
                            row('FWRY', 'd', freshness='UNKNOWN')]))
    assert (levels['observed_symbols'], levels['admitted_symbols'], levels['current_symbols'],
            levels['admitted_current_symbols']) == (3, 2, 2, 1)


def test_unavailable_inputs_are_unknown_not_zero():
    levels = coverage_breakdown(security_master=None, daily_observations=None, scan_runs=None)
    assert all(item['count'] is None for item in levels)
    html = render_coverage({'EGX': levels})
    assert '<td>0</td>' not in html and html.count('<td>UNKNOWN</td>') == len(LEVELS)


def test_historical_scan_run_is_labelled_historical():
    run = {'status': 'HISTORICAL_RUN', 'run': {
        'completed_at': '2026-09-24T12:00:00+00:00', 'scanned': 5,
        'status_counts': {'WATCH': 2, 'READY_NO_SIGNAL': 3}}}
    levels = {item['level']: item for item in coverage_breakdown(
        security_master=MASTER, daily_observations=[], scan_runs=run)}
    assert levels['scanned_symbols']['count'] == 5
    assert levels['candidates']['count'] == 2
    assert 'historical' in levels['scanned_symbols']['basis']
    assert 'not a fill' in levels['candidates']['basis']


def test_render_escapes_and_states_non_implication():
    html = render_coverage({'EGX': unknown_breakdown('<script>')})
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'does not imply the next row' in html and 'not zero' in html


def operational():
    return {'configured': True, 'available': True, 'status': 'DATA_STALE',
            'observed_at': None, 'symbols': []}


def test_product_state_and_today_render_breakdown_for_both_markets():
    state = product_state(operational(), security_master=MASTER, scan_history=None,
                          daily_observations=[row('COMI', 'tradingview_tvdatafeed')])
    assert counts(state['coverage_breakdown']['EGX'])['identities'] == 319
    assert all(item['count'] is None for item in state['coverage_breakdown']['US'])
    html = render_product(state)
    assert 'EGX coverage breakdown' in html and 'US coverage breakdown' in html
    assert 'A candidate is not a fill' in html and '!=' not in html
    assert 'Authoritative current exchange universe' in html
    assert 'Operational receipt status: DATA_STALE' in html


def test_breakdown_shown_only_in_today_swing_system():
    for section, shown in (('TODAY', True), ('SWING', True), ('SYSTEM', True),
                           ('LIVE', False), ('PERFORMANCE', False)):
        html = render_product(product_state(operational(), section=section,
                                            security_master=MASTER))
        assert ('aria-label="EGX coverage breakdown"' in html) is shown, section
