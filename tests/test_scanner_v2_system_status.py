"""Code-declared Scanner v2 SYSTEM status; not runtime, scan or card evidence."""
from copy import deepcopy
import json
from unittest.mock import patch

from app.scanner_v2_card_report import (
    API_ROUTE, FEATURE_STATUS, LIMITATIONS, SCHEMA_VERSION, feature_status)
from app.ui.system import load_system_state, render_scanner_v2, render_system


def test_feature_status_is_fixed_inactive_and_fresh():
    status = feature_status()
    assert status['status'] == FEATURE_STATUS == 'OFFLINE_PROJECTION_NOT_ACTIVATED'
    assert status['schema_version'] == SCHEMA_VERSION
    assert status['market_adapters'] == {'EGX': 'OFFLINE_PROJECTION_ONLY',
                                         'US': 'NOT_IMPLEMENTED'}
    assert status['api_route'] == API_ROUTE == '/api/scanner-v2/egx/card-report'
    assert status['runtime_producer'] is None
    assert status['published_cards'] is None
    assert status['live_money'] is status['execution_allowed'] is False
    assert status['runtime_activation'] is False
    assert status['mode'] == 'SHADOW' and status['promotion'] == 'NOT_AUTHORIZED'
    assert status['limitations'] == list(LIMITATIONS)
    json.dumps(status, allow_nan=False)
    status['limitations'].append('MUTATED')
    status['market_adapters']['US'] = 'MUTATED'
    assert feature_status()['limitations'] == list(LIMITATIONS)
    assert feature_status()['market_adapters']['US'] == 'NOT_IMPLEMENTED'


def test_system_state_and_page_expose_status_without_runtime_claims():
    with patch.dict('os.environ', {}, clear=True):
        state = load_system_state()
    assert state['scanner_v2'] == feature_status()
    assert state['live_money'] is False
    before = deepcopy(state)
    html = render_scanner_v2(state)
    assert 'Scanner v2 Trade Cards: OFFLINE_PROJECTION_NOT_ACTIVATED' in html
    assert 'API route</th><td>' + API_ROUTE in html
    assert 'Runtime producer</th><td>NOT_INSTALLED' in html
    assert 'Published cards</th><td>UNKNOWN' in html
    assert 'US adapter</th><td>NOT_IMPLEMENTED' in html
    assert 'never fills' in html and 'LIVE_MONEY=DISABLED' in html
    for limitation in LIMITATIONS:
        assert limitation in html
    assert html in render_system(state)
    assert state == before


def test_render_escapes_and_degrades_to_unknown():
    assert 'Scanner v2 Trade Cards: UNKNOWN' in render_scanner_v2({})
    assert 'Scanner v2 Trade Cards: UNKNOWN' in render_scanner_v2({'scanner_v2': 'x'})
    forged = feature_status() | {'status': '<script>x</script>', 'market_adapters': None,
                                 'limitations': None}
    html = render_scanner_v2({'scanner_v2': forged})
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'EGX adapter</th><td>UNKNOWN' in html
    assert 'Limitations: UNKNOWN' in html
