"""SYSTEM daily source admission view; repository declarations only."""
from app.data.source_admission import (
    DAILY_SOURCE_DECLARATIONS, DailySourceDeclaration, DailySourceRegistry, DataDelay,
    EntitlementStatus, SourceAccess, daily_source_summary,
)
from app.ui import system


def test_summary_lists_every_declaration_and_none_admitted():
    rows = daily_source_summary()
    assert {(r['provider'], r['market']) for r in rows} == {
        (d.provider, d.market) for d in DAILY_SOURCE_DECLARATIONS}
    assert all(r['status'] == 'EVIDENCE_BLOCKED' for r in rows)
    eodhd = next(r for r in rows if r['provider'] == 'eodhd')
    assert (eodhd['access'], eodhd['reason']) == (
        'PAID_SUBSCRIPTION', 'paid subscription source not admissible')


def test_summary_resolves_admission_for_injected_registry():
    registry = DailySourceRegistry((DailySourceDeclaration(
        provider='fixture_free', market='EGX', access=SourceAccess.ANONYMOUS_PUBLIC,
        entitlement=EntitlementStatus.REVIEWED_PAPER_SHADOW, delay=DataDelay.END_OF_DAY,
        source_timezone='Africa/Cairo', evidence='artificial fixture review'),))
    [row] = daily_source_summary(registry)
    assert (row['status'], row['reason']) == ('ADMITTED', 'reviewed paper/shadow entitlement')


def test_system_state_and_html_expose_source_admission(monkeypatch):
    for key in ('EGX_PAPER_RUNTIME', 'EGX_SCHEDULER_HEARTBEAT_PATH', 'EGX_SCAN_HISTORY_PATH'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('EGX_DB_PATH', '/nonexistent/platform.db')
    state = system.load_system_state()
    assert state['daily_sources'] == daily_source_summary()
    html = system.render_system(state)
    assert 'Daily source admission' in html
    assert f'Admitted daily sources: 0 of {len(DAILY_SOURCE_DECLARATIONS)} declared.' in html
    assert '<td>tradingview_tvdatafeed_egx</td>' in html
    assert 'Undeclared sources are EVIDENCE_BLOCKED' in html
    # Unconfigured runtime: components are explicit, never READY.
    assert state['readiness']['scan_readiness'] == 'EVIDENCE_BLOCKED'
    assert state['readiness']['scheduler_heartbeat'] == state['scheduler']['status']
    assert 'EGX readiness' in html


def test_source_rows_are_escaped():
    html = system.render_daily_sources([dict.fromkeys(
        ('provider', 'market', 'access', 'entitlement', 'delay', 'status', 'reason',
         'evidence'), '<b>x</b>')])
    assert '<b>x</b>' not in html and '&lt;b&gt;x&lt;/b&gt;' in html
