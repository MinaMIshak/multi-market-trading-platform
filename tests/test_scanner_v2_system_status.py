"""Code-declared Scanner v2 SYSTEM status; not runtime, scan or card evidence."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
from unittest.mock import patch

from app.scanner_v2_card_report import (
    API_ROUTE, FEATURE_STATUS, LIMITATIONS, PRODUCER_ENTRYPOINT, PRODUCER_SCHEDULE,
    SCHEMA_VERSION, feature_status)
from app.ui.system import load_system_state, render_scanner_v2, render_system

ROOT = Path(__file__).resolve().parents[1]


def test_feature_status_is_fixed_inactive_and_fresh():
    status = feature_status()
    assert status['status'] == FEATURE_STATUS == 'OFFLINE_PROJECTION_NOT_ACTIVATED'
    assert status['schema_version'] == SCHEMA_VERSION
    assert status['market_adapters'] == {'EGX': 'OFFLINE_PROJECTION_ONLY',
                                         'US': 'NOT_IMPLEMENTED'}
    assert status['api_route'] == API_ROUTE == '/api/scanner-v2/egx/card-report'
    assert status['producer_entrypoint'] == PRODUCER_ENTRYPOINT
    assert status['producer_schedule'] == PRODUCER_SCHEDULE == 'NOT_SCHEDULED_IN_REPOSITORY'
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
    assert 'Producer entrypoint</th><td>' + PRODUCER_ENTRYPOINT in html
    assert 'Producer schedule</th><td>NOT_SCHEDULED_IN_REPOSITORY' in html
    assert 'Runtime producer</th><td>NOT_INSTALLED' in html
    assert 'Published cards</th><td>UNKNOWN' in html
    assert 'US adapter</th><td>NOT_IMPLEMENTED' in html
    assert 'never fills' in html and 'LIVE_MONEY=DISABLED' in html
    for limitation in LIMITATIONS:
        assert limitation in html
    assert html in render_system(state)
    assert state == before


def test_declared_producer_entrypoint_is_real_and_unscheduled():
    prefix = 'python -m '
    assert PRODUCER_ENTRYPOINT.startswith(prefix)
    module = importlib.import_module(PRODUCER_ENTRYPOINT[len(prefix):])
    assert callable(module.main) and callable(module.run_egx_card_report_job)
    # The status claims no repository schedule: no scheduler/cron/delivery
    # schedule source in app/ or tools/ may name the job module.
    name = PRODUCER_ENTRYPOINT[len(prefix):]
    sources = [path for base in ('app', 'tools') for pattern in ('*schedul*', '*cron*')
               for path in (ROOT / base).rglob(pattern)
               if path.is_file() and path.suffix == '.py']
    assert ROOT / 'app' / 'core' / 'schedule.py' in sources
    assert ROOT / 'tools' / 'delivery_schedule.py' in sources
    for path in sources:
        assert name not in path.read_text(encoding='utf-8'), path


def test_degrades_when_producer_fields_missing():
    forged = {k: v for k, v in feature_status().items()
              if k not in ('producer_entrypoint', 'producer_schedule')}
    html = render_scanner_v2({'scanner_v2': forged})
    assert 'Producer entrypoint</th><td>NOT_INSTALLED' in html
    assert 'Producer schedule</th><td>UNKNOWN' in html


def test_render_escapes_and_degrades_to_unknown():
    assert 'Scanner v2 Trade Cards: UNKNOWN' in render_scanner_v2({})
    assert 'Scanner v2 Trade Cards: UNKNOWN' in render_scanner_v2({'scanner_v2': 'x'})
    forged = feature_status() | {'status': '<script>x</script>', 'market_adapters': None,
                                 'limitations': None}
    html = render_scanner_v2({'scanner_v2': forged})
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'EGX adapter</th><td>UNKNOWN' in html
    assert 'Limitations: UNKNOWN' in html
