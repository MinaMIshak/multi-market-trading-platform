"""Component readiness contracts; artificial inputs, never runtime evidence."""
import pytest

from app.ui.product import product_state, render_product
from app.ui.readiness import egx_readiness, render_readiness

UNCONFIGURED = {'configured': False, 'available': False, 'status': 'NOT_READY', 'symbols': []}
IDENTITIES = {'total_instruments': 318, 'by_type': {'EQUITY': 311, 'INDEX': 7},
              'source_providers': ['egid'], 'latest_snapshot_updated_at': None,
              'latest_source_market_date': None}
DAILY = [{'canonical_symbol': 'COMI', 'provider': 'tradingview_tvdatafeed_egx',
          'source_snapshot_date': '2026-09-24', 'oldest_market_date': '2025-01-27',
          'newest_market_date': '2026-09-24', 'valid_bar_count': 400,
          'quarantined_bar_count': 0, 'freshness': 'STALE'}]


def admitted(rows):
    return [dict(r, source_status='ADMITTED') for r in rows]


def test_runtime_snapshot_scenario_is_partial_not_ready():
    # Identities and bars present; no admitted source, heartbeat, history or receipts.
    state = product_state(UNCONFIGURED, market='EGX', security_master=IDENTITIES,
                          daily_observations=DAILY, heartbeat={'status': 'UNKNOWN'},
                          scan_history={'status': 'UNKNOWN'})
    readiness = state['readiness']['EGX']
    assert readiness['canonical_data'] == 'AVAILABLE'
    assert readiness['security_master'] == {'status': 'AVAILABLE', 'instruments': 318,
                                            'equities': 311}
    assert readiness['daily_observations']['status'] == 'AVAILABLE'
    assert readiness['daily_observations']['freshness'] == {'STALE': 1}
    assert readiness['source_admission'] == 'NO_ADMITTED_SOURCE'
    assert readiness['scan_readiness'] == 'EVIDENCE_BLOCKED'
    assert readiness['blockers'] == [
        'NO_ADMITTED_DAILY_SOURCE', 'SCHEDULER_HEARTBEAT_UNAVAILABLE',
        'SCAN_HISTORY_UNAVAILABLE', 'OPERATIONAL_RECEIPTS_NOT_CONFIGURED']
    # Receipt-reader fields are preserved, not upgraded by data availability.
    assert state['markets']['EGX']['status'] == 'NOT_READY'
    assert state['coverage']['EGX']['data_ready'] is None
    page = render_product(state)
    assert 'EGX readiness' in page
    assert '<th scope="row">Scan readiness</th><td>EVIDENCE_BLOCKED</td>' in page
    assert '<th scope="row">Operational receipts</th><td>NOT CONFIGURED</td>' in page
    assert 'AVAILABLE · 318 instruments' in page


def test_row_source_claims_cannot_unblock_readiness():
    state = product_state(UNCONFIGURED, market='EGX', security_master=IDENTITIES,
                          daily_observations=admitted(DAILY))
    assert state['readiness']['EGX']['source_admission'] == 'NO_ADMITTED_SOURCE'


def test_admitted_source_alone_is_not_ready():
    result = egx_readiness(operational=UNCONFIGURED, security_master=IDENTITIES,
                           daily_observations=admitted(DAILY),
                           heartbeat={'status': 'STALE'}, scan_history=None)
    assert result['scan_readiness'] == 'NOT_READY'
    assert 'SCHEDULER_HEARTBEAT_STALE' in result['blockers']


def test_all_prerequisites_never_report_ready():
    operational = {'configured': True, 'available': True, 'status': 'OPERATIONAL'}
    result = egx_readiness(operational=operational, security_master=IDENTITIES,
                           daily_observations=admitted(DAILY),
                           heartbeat={'status': 'RECENT_POLL'},
                           scan_history={'status': 'HISTORICAL_RUN'})
    assert result['blockers'] == []
    assert result['scan_readiness'] == 'PREREQUISITES_MET'


@pytest.mark.parametrize('identities,daily,expected', [
    (None, None, 'UNAVAILABLE'), (IDENTITIES, None, 'PARTIAL'),
    (None, DAILY, 'PARTIAL'), (IDENTITIES, [], 'PARTIAL'),
])
def test_missing_canonical_inputs_are_explicit(identities, daily, expected):
    result = egx_readiness(operational=UNCONFIGURED, security_master=identities,
                           daily_observations=daily, heartbeat=None, scan_history=None)
    assert result['canonical_data'] == expected
    assert result['scan_readiness'] == 'EVIDENCE_BLOCKED'
    if daily is None:
        assert result['source_admission'] == 'UNKNOWN'
        assert 'DAILY_OBSERVATIONS_UNAVAILABLE' in result['blockers']


def test_us_readiness_unknown_and_html_escaped():
    state = product_state(UNCONFIGURED, market='US', security_master=IDENTITIES,
                          daily_observations=DAILY)
    assert state['readiness'] == {'US': None}
    assert 'US readiness: UNKNOWN' in render_product(state)
    hostile = egx_readiness(operational={'configured': True, 'available': True,
                                         'status': '<b>x</b>'},
                            security_master=None, daily_observations=None,
                            heartbeat={'status': '<i>y</i>'}, scan_history=None)
    html = render_readiness({'EGX': hostile})
    assert '<b>' not in html and '<i>' not in html


def test_readiness_is_not_shown_on_live_or_performance():
    for section in ('LIVE', 'PERFORMANCE', 'PRE-SURGE'):
        page = render_product(product_state(UNCONFIGURED, market='EGX', section=section,
                                            security_master=IDENTITIES,
                                            daily_observations=DAILY))
        assert 'EGX readiness' not in page
