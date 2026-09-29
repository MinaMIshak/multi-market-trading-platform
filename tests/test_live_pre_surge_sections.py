"""LIVE / PRE-SURGE product contracts; artificial rows, never market evidence."""
from app.ui.product import product_state, render_product
from app.ui.sections import PRE_SURGE_CONTRACT, pre_surge_state

SOURCE = {'configured': False, 'available': False, 'status': 'NOT_READY', 'symbols': []}
DAILY = [{'canonical_symbol': 'COMI', 'provider': 'eodhd', 'source_snapshot_date': '2026-09-24',
          'oldest_market_date': '2025-01-27', 'newest_market_date': '2026-09-24',
          'valid_bar_count': 400, 'quarantined_bar_count': 0, 'freshness': 'STALE'},
         {'canonical_symbol': 'SWDY', 'provider': 'unknown_feed', 'source_snapshot_date': '2026-09-24',
          'oldest_market_date': '2025-01-27', 'newest_market_date': '2026-09-23',
          'valid_bar_count': 300, 'quarantined_bar_count': 1, 'freshness': 'UNKNOWN'}]


def test_live_shows_declared_delay_and_never_real_time():
    state = product_state(SOURCE, market='EGX', section='LIVE', daily_observations=DAILY)
    # The LIVE_MONEY safety label is never overwritten by LIVE monitoring state.
    assert state['live'] == 'DISABLED'
    live = state['live_monitoring']['EGX']
    assert live['real_time'] is False
    assert live['near_current_feed']['status'] == 'UNAVAILABLE'
    assert [(r['symbol'], r['source_delay'], r['latest_market_date'], r['freshness'],
             r['source_status']) for r in live['daily_observations']] == [
        ('COMI', 'END_OF_DAY', '2026-09-24', 'STALE', 'EVIDENCE_BLOCKED'),
        ('SWDY', 'UNKNOWN', '2026-09-23', 'UNKNOWN', 'EVIDENCE_BLOCKED')]
    page = render_product(state)
    assert '<td>END_OF_DAY</td>' in page and 'Nothing below is real-time' in page


def test_live_unknown_and_empty_are_distinct_and_us_unknown():
    assert product_state(SOURCE, market='EGX')['live_monitoring']['EGX'][
        'daily_observations'] is None
    empty = render_product(product_state(SOURCE, market='EGX', section='LIVE',
                                         daily_observations=[]))
    assert 'Daily observations: none recorded' in empty
    us = product_state(SOURCE, market='US', section='LIVE', daily_observations=DAILY)
    assert us['live_monitoring'] == {'US': None} and us['live'] == 'DISABLED'
    assert 'US LIVE monitoring: UNKNOWN' in render_product(us)


def test_pre_surge_blocked_without_admitted_source_and_scorer_rows():
    state = product_state(SOURCE, market='EGX', section='PRE-SURGE', daily_observations=DAILY)
    pre = state['pre_surge']['EGX']
    assert pre['status'] == 'EVIDENCE_BLOCKED'
    assert pre['blockers'] == ['NO_ADMITTED_DAILY_SOURCE', 'NO_ATTESTED_SCORER_ROWS']
    assert pre['candidates'] is None and pre['scorer_rows_connected'] is False
    page = render_product(state)
    assert 'EGX PRE-SURGE: EVIDENCE_BLOCKED' in page
    assert 'not a prediction' in page
    assert 'NONE SHIPPED' in page


def test_pre_surge_with_admitted_source_is_still_unavailable_without_scorer():
    pre = pre_surge_state({'source_admission': 'ADMITTED_SOURCE_PRESENT'})
    assert (pre['status'], pre['blockers']) == ('UNAVAILABLE', ['NO_ATTESTED_SCORER_ROWS'])
    assert pre_surge_state(None)['status'] == 'EVIDENCE_BLOCKED'


def test_pre_surge_contract_matches_engine_formula():
    import inspect
    from app.strategies import eod
    source = inspect.getsource(eod.PreSurgeV7Engine.evaluate)
    for fragment in ('.25 * ranks', '.15 * ranks', '.05 * ranks', 'today_return < 0.05',
                     'risk_penalty * ranks'):
        assert fragment in source
    assert PRE_SURGE_CONTRACT['scorer_contract'] == 'LEGACY_V5_PARITY_SEED'


def test_pre_surge_hidden_on_other_sections():
    for section in ('TODAY', 'SWING', 'LIVE'):
        page = render_product(product_state(SOURCE, market='EGX', section=section,
                                            daily_observations=DAILY))
        assert 'EGX PRE-SURGE:' not in page
