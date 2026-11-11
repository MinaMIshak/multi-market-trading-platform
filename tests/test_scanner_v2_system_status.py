"""Code-declared Scanner v2 SYSTEM status; not runtime, scan or card evidence."""
from copy import deepcopy
import importlib
import inspect
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.scanner_v2_card_report import (
    API_ROUTE, CARD_PAGE_ROUTE, EVIDENCE_RETENTION_ENTRYPOINT, EVIDENCE_RETENTION_SCHEDULE, FEATURE_STATUS,
    LIMITATIONS, PRODUCER_ENTRYPOINT, PRODUCER_SCHEDULE, SCHEMA_VERSION, US_ADAPTER_GAPS,
    feature_status)
from app.ui.system import load_system_state, render_scanner_v2, render_system

ROOT = Path(__file__).resolve().parents[1]


def test_feature_status_is_fixed_inactive_and_fresh():
    status = feature_status()
    assert status['status'] == FEATURE_STATUS == 'OFFLINE_PROJECTION_NOT_ACTIVATED'
    assert status['schema_version'] == SCHEMA_VERSION
    assert status['market_adapters'] == {'EGX': 'OFFLINE_PROJECTION_ONLY',
                                         'US': 'NOT_IMPLEMENTED'}
    assert status['api_route'] == API_ROUTE == '/api/scanner-v2/egx/card-report'
    assert status['card_page'] == CARD_PAGE_ROUTE == '/scanner-v2/egx/cards'
    assert status['producer_entrypoint'] == PRODUCER_ENTRYPOINT
    assert status['producer_schedule'] == PRODUCER_SCHEDULE == 'NOT_SCHEDULED_IN_REPOSITORY'
    assert status['evidence_retention_entrypoint'] == EVIDENCE_RETENTION_ENTRYPOINT
    assert (status['evidence_retention_schedule'] == EVIDENCE_RETENTION_SCHEDULE
            == 'NOT_SCHEDULED_IN_REPOSITORY')
    assert status['runtime_producer'] is None
    assert status['published_cards'] is None
    assert status['live_money'] is status['execution_allowed'] is False
    assert status['runtime_activation'] is False
    assert status['mode'] == 'SHADOW' and status['promotion'] == 'NOT_AUTHORIZED'
    assert status['limitations'] == list(LIMITATIONS)
    assert status['us_adapter_gaps'] == list(US_ADAPTER_GAPS)
    json.dumps(status, allow_nan=False)
    status['limitations'].append('MUTATED')
    status['market_adapters']['US'] = 'MUTATED'
    status['us_adapter_gaps'].clear()
    assert feature_status()['limitations'] == list(LIMITATIONS)
    assert feature_status()['market_adapters']['US'] == 'NOT_IMPLEMENTED'
    assert feature_status()['us_adapter_gaps'] == list(US_ADAPTER_GAPS)


def test_declared_us_adapter_gaps_match_the_code():
    # Each declared gap is checked against the repository, so the list cannot
    # silently outlive the change that closes it.
    assert US_ADAPTER_GAPS == (
        'US_DECISION_ADAPTER_NOT_IMPLEMENTED', 'US_INSTRUMENT_IDENTITY_NOT_BOUND',
        'US_CALENDAR_OBSERVATION_NOT_RETAINED', 'EGX_ADAPTER_RULES_NOT_REUSABLE_FOR_US')
    # US_DECISION_ADAPTER_NOT_IMPLEMENTED: no US Scanner v2 module exists.
    assert not [p for p in (ROOT / 'app').glob('scanner_v2*.py') if 'us' in p.stem.split('_')]
    # US_INSTRUMENT_IDENTITY_NOT_BOUND: the daily US pipeline binds no UUID.
    us_run = (ROOT / 'app' / 'us_run.py').read_text(encoding='utf-8').lower()
    assert 'instrument_id' not in us_run and 'uuid' not in us_run
    # US_CALENDAR_OBSERVATION_NOT_RETAINED: retention projects EGX only.
    from app.storage.repository import TradingRepository
    import app.scanner_v2_calendar as calendar
    assert 'project_egx_calendar' in inspect.getsource(TradingRepository.retain_calendar_observation)
    assert [n for n in vars(calendar) if n.startswith('project_')] == ['project_egx_calendar']
    # EGX_ADAPTER_RULES_NOT_REUSABLE_FOR_US: pinned EGX rules, costs and Cairo dates.
    from app import scanner_v2_egx_rank as egx_rank
    from app.strategies.egx_ranking import US_PROFILE
    from app.us_run import US_COMMISSION, US_SLIPPAGE
    assert egx_rank.RULES['rank_version'] == 'EGX-RANK-v1' != US_PROFILE.version
    assert (egx_rank.RULES['slippage'], egx_rank.RULES['commission']) != (
        str(US_SLIPPAGE), str(US_COMMISSION))
    assert str(egx_rank.CAIRO) == 'Africa/Cairo'


def test_system_state_and_page_expose_status_without_runtime_claims():
    with patch.dict('os.environ', {}, clear=True):
        state = load_system_state()
    assert state['scanner_v2'] == feature_status()
    assert state['live_money'] is False
    before = deepcopy(state)
    html = render_scanner_v2(state)
    assert 'Scanner v2 Trade Cards: OFFLINE_PROJECTION_NOT_ACTIVATED' in html
    assert 'API route</th><td>' + API_ROUTE in html
    assert 'Card page</th><td>' + CARD_PAGE_ROUTE in html
    assert 'Producer entrypoint</th><td>' + PRODUCER_ENTRYPOINT in html
    assert 'Producer schedule</th><td>NOT_SCHEDULED_IN_REPOSITORY' in html
    assert 'Evidence retention entrypoint</th><td>' + EVIDENCE_RETENTION_ENTRYPOINT in html
    assert 'Evidence retention schedule</th><td>NOT_SCHEDULED_IN_REPOSITORY' in html
    assert 'Runtime producer</th><td>NOT_INSTALLED' in html
    assert 'Published cards</th><td>UNKNOWN' in html
    assert 'US adapter</th><td>NOT_IMPLEMENTED' in html
    assert 'US adapter gaps</th><td>' + ', '.join(US_ADAPTER_GAPS) in html
    assert 'never fills' in html and 'LIVE_MONEY=DISABLED' in html
    for limitation in LIMITATIONS:
        assert limitation in html
    page = render_system(state)
    assert html in page and f'<a href="{CARD_PAGE_ROUTE}">' in page
    assert state == before


@pytest.mark.parametrize('entrypoint,runner', [
    (PRODUCER_ENTRYPOINT, 'run_egx_card_report_job'),
    (EVIDENCE_RETENTION_ENTRYPOINT, 'run_egx_evidence_retention_job'),
])
def test_declared_entrypoints_are_real_and_unscheduled(entrypoint, runner):
    prefix = 'python -m '
    assert entrypoint.startswith(prefix)
    module = importlib.import_module(entrypoint[len(prefix):])
    assert callable(module.main) and callable(getattr(module, runner))
    # The status claims no repository schedule: no scheduler/cron/delivery
    # schedule source in app/ or tools/ may name the job module.
    name = entrypoint[len(prefix):]
    sources = [path for base in ('app', 'tools') for pattern in ('*schedul*', '*cron*')
               for path in (ROOT / base).rglob(pattern)
               if path.is_file() and path.suffix == '.py']
    assert ROOT / 'app' / 'core' / 'schedule.py' in sources
    assert ROOT / 'tools' / 'delivery_schedule.py' in sources
    for path in sources:
        assert name not in path.read_text(encoding='utf-8'), path


def test_degrades_when_producer_fields_missing():
    forged = {k: v for k, v in feature_status().items()
              if k not in ('card_page', 'producer_entrypoint', 'producer_schedule',
                           'evidence_retention_entrypoint', 'evidence_retention_schedule')}
    html = render_scanner_v2({'scanner_v2': forged})
    assert 'Card page</th><td>NOT_INSTALLED' in html
    assert 'Producer entrypoint</th><td>NOT_INSTALLED' in html
    assert 'Producer schedule</th><td>UNKNOWN' in html
    assert 'Evidence retention entrypoint</th><td>NOT_INSTALLED' in html
    assert 'Evidence retention schedule</th><td>UNKNOWN' in html


def test_render_escapes_and_degrades_to_unknown():
    assert 'Scanner v2 Trade Cards: UNKNOWN' in render_scanner_v2({})
    assert 'Scanner v2 Trade Cards: UNKNOWN' in render_scanner_v2({'scanner_v2': 'x'})
    forged = feature_status() | {'status': '<script>x</script>', 'market_adapters': None,
                                 'limitations': None, 'us_adapter_gaps': ['<b>x</b>']}
    html = render_scanner_v2({'scanner_v2': forged})
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '<b>' not in html and 'US adapter gaps</th><td>&lt;b&gt;x&lt;/b&gt;' in html
    assert 'EGX adapter</th><td>UNKNOWN' in html
    for missing in (None, 'x', []):
        html = render_scanner_v2({'scanner_v2': feature_status() | {'us_adapter_gaps': missing}})
        assert 'US adapter gaps</th><td>UNKNOWN' in html
    html = render_scanner_v2({'scanner_v2': feature_status() | {'limitations': None}})
    assert 'Limitations: UNKNOWN' in html
