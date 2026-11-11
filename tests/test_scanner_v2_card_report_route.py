"""Read-only Scanner v2 card-report route and HTML page over the protected loader.

Artificial retained reports in temporary directories only. No producer, market
coverage, admission, fill or edge evidence is implied.
"""
import json

import app.main as main
from app.scanner_v2_card_report import API_ROUTE, CARD_PAGE_ROUTE, LIMITATIONS, feature_status
from app.scanner_v2_card_report_loader import (
    ENV, RESPONSE_SCHEMA, load_retained_egx_card_report)
from app.ui.trade_card import render_card_report_page
from tests.test_scanner_v2_card_report import VIEW, project
from tests.test_scanner_v2_card_report_loader import (
    SAFETY, assert_unavailable, encode, publish)
from tests.test_scanner_v2_egx_rank import record, report
from tests.test_scanner_v2_retained_binding import OPEN


def test_route_is_single_read_only_get_named_in_feature_status():
    routes = [route for route in main.app.routes if getattr(route, 'path', None) == API_ROUTE]
    assert len(routes) == 1
    assert routes[0].methods == {'GET'}
    assert routes[0].endpoint is main.scanner_v2_egx_card_report
    assert feature_status()['api_route'] == API_ROUTE
    # Installing the route is not activation: no producer, no published cards.
    assert feature_status()['status'] == 'OFFLINE_PROJECTION_NOT_ACTIVATED'
    assert feature_status()['runtime_producer'] is None
    assert feature_status()['published_cards'] is None


def test_route_not_configured_by_default(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    out = main.scanner_v2_egx_card_report()
    assert out['schema_version'] == RESPONSE_SCHEMA
    assert out['status'] == 'NOT_CONFIGURED' and out['report'] is None
    assert {k: out[k] for k in SAFETY} == SAFETY
    json.dumps(out, allow_nan=False)


def test_route_fails_closed_on_invalid_or_unpublished_directory(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV, 'relative/path')
    assert_unavailable(main.scanner_v2_egx_card_report(), 'CONFIG_INVALID')
    monkeypatch.setenv(ENV, str(tmp_path))
    assert_unavailable(main.scanner_v2_egx_card_report(), 'NOT_PUBLISHED')
    publish(tmp_path, b'{}')
    out = main.scanner_v2_egx_card_report()
    assert_unavailable(out, 'REPORT_REJECTED')
    json.dumps(out, allow_nan=False)


def test_route_serves_exact_loader_response_without_mutation(monkeypatch, tmp_path):
    payload = encode(project())
    digest = publish(tmp_path, payload)
    monkeypatch.setenv(ENV, str(tmp_path))
    # Pin the loader clock to the fixture view; the route adds nothing of its own.
    monkeypatch.setattr(main, 'load_retained_egx_card_report',
                        lambda: load_retained_egx_card_report(now=VIEW))
    before = sorted(path.name for path in tmp_path.iterdir())
    out = main.scanner_v2_egx_card_report()
    assert out == load_retained_egx_card_report(now=VIEW)
    assert out['status'] == 'AVAILABLE' and out['report_sha256'] == digest
    assert out['report'] == json.loads(payload)
    assert {k: out[k] for k in SAFETY} == SAFETY
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    json.dumps(out, allow_nan=False)


def serve_page(monkeypatch, directory, payload, now):
    publish(directory, payload)
    monkeypatch.setenv(ENV, str(directory))
    monkeypatch.setattr(main, 'load_retained_egx_card_report',
                        lambda: load_retained_egx_card_report(now=now))
    return main.scanner_v2_egx_card_page().body.decode()


def test_card_page_is_single_read_only_get_named_in_feature_status():
    routes = [route for route in main.app.routes
              if getattr(route, 'path', None) == CARD_PAGE_ROUTE]
    assert len(routes) == 1
    assert routes[0].methods == {'GET'}
    assert routes[0].endpoint is main.scanner_v2_egx_card_page
    assert feature_status()['card_page'] == CARD_PAGE_ROUTE
    # Installing a page is not activation: no producer, no published cards.
    assert feature_status()['status'] == 'OFFLINE_PROJECTION_NOT_ACTIVATED'
    assert feature_status()['runtime_producer'] is None
    assert feature_status()['published_cards'] is None


def test_card_page_shows_no_cards_unless_report_is_accepted(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV, raising=False)
    html = main.scanner_v2_egx_card_page().body.decode()
    assert 'Report: NOT_CONFIGURED' in html and 'LIVE_MONEY=DISABLED' in html
    assert 'This does not mean that no candidates exist' in html
    assert 'Trade Card:' not in html
    monkeypatch.setenv(ENV, str(tmp_path))
    html = main.scanner_v2_egx_card_page().body.decode()
    assert 'Report: UNAVAILABLE' in html and 'NOT_PUBLISHED' in html
    assert 'Trade Card:' not in html
    publish(tmp_path, b'{}')
    html = main.scanner_v2_egx_card_page().body.decode()
    assert 'REPORT_REJECTED' in html and 'Trade Card:' not in html


def test_card_page_renders_accepted_cards_and_truth_labels(monkeypatch, tmp_path):
    doc = project()
    payload = encode(doc)
    html = serve_page(monkeypatch, tmp_path, payload, VIEW)
    before = sorted(path.name for path in tmp_path.iterdir())
    assert 'Trade Card: AAAA' in html and 'Trade Card: BBBB' in html
    assert 'Report: WITHIN_RETAINED_WINDOW' in html
    assert html.count('At load: CURRENT') == 2 and 'At load: NOT_CURRENT' not in html
    assert 'Cards current at load</th><td>2 of 2' in html
    assert 'Authoritative universe size</th><td>UNKNOWN' in html
    assert 'Requested</th><td>5' in html and 'Excluded</th><td>3' in html
    assert doc['provenance']['artifact_sha256'] in html
    assert 'Identity admission</th><td>INTERNAL_SNAPSHOT_VERIFIED' in html
    assert 'Calendar admission</th><td>NOT_ESTABLISHED' in html
    assert 'Lifecycle evidence</th><td>NOT_VERIFIED' in html
    assert 'Sample size</th><td>UNKNOWN' in html and 'Net result</th><td>UNKNOWN' in html
    assert 'Promotion</th><td>NOT_AUTHORIZED' in html and 'LIVE_MONEY=DISABLED' in html
    for limitation in LIMITATIONS:
        assert limitation in html
    for forbidden in ('>BUY', '>SELL', 'FILLED', 'expected_return'):
        assert forbidden not in html
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_card_page_never_presents_old_report_as_current(monkeypatch, tmp_path):
    html = serve_page(monkeypatch, tmp_path, encode(project()), OPEN)
    assert 'Report: STALE_AT_LOAD' in html and 'historical' in html
    assert html.count('At load: NOT_CURRENT') == 2 and 'At load: CURRENT' not in html
    assert 'Cards current at load</th><td>0 of 2' in html
    # Producer-time labels are shown unchanged, under the at-load state.
    assert html.count('SHADOW / RESEARCH_CANDIDATE') == 2
    assert html.index('At load: NOT_CURRENT') < html.index('SHADOW / RESEARCH_CANDIDATE')


def test_card_page_reports_currency_per_card(tmp_path, monkeypatch):
    publish(tmp_path, encode(project()))
    monkeypatch.setenv(ENV, str(tmp_path))
    out = load_retained_egx_card_report(now=VIEW)
    plan = out['report']['cards'][0]['strategy_inference']['plan']
    plan['valid_until'] = VIEW.isoformat()
    out['cards_current_at_load'] = 1
    html = render_card_report_page(out)
    assert html.count('At load: CURRENT') == 1 and html.count('At load: NOT_CURRENT') == 1
    assert html.index('At load: NOT_CURRENT') < html.index('Trade Card: AAAA')


def test_card_page_with_no_cards_keeps_scope_caveat(monkeypatch, tmp_path):
    artifact = report([record('AAAA', 'WATCHLIST'), record('BBBB', 'NO_TRADE')])
    html = serve_page(monkeypatch, tmp_path, encode(project(artifact=artifact)), VIEW)
    assert 'No Trade Cards in this report' in html and 'Trade Card:' not in html
    assert 'not the authoritative EGX universe' in html


def test_card_page_lists_each_exclusion_with_its_reason(monkeypatch, tmp_path):
    html = serve_page(monkeypatch, tmp_path, encode(project()), VIEW)
    section = html[html.index('<h2>Excluded symbols: 3 of 5</h2>'):]
    for row in (('CCCC', 'WATCHLIST', 'SETUP_NOT_CONFIRMED', 'NONE_REPORTED'),
                ('DDDD', 'NO_TRADE', 'NO_TRADE', 'NONE_REPORTED'),
                ('EEEE', 'CANDIDATE', 'IDENTITY_UNBOUND', 'IDENTITY_MISSING')):
        assert '<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' in section
    # Symbols with cards are not exclusions, and cards come before this list.
    assert '<td>AAAA</td>' not in section and '<td>BBBB</td>' not in section
    assert html.index('Trade Card: BBBB') < html.index('Excluded symbols: 3 of 5')


def test_card_page_omits_exclusions_when_none_or_report_rejected(monkeypatch, tmp_path):
    artifact = report([record('AAAA', 'CANDIDATE'), record('BBBB', 'STRONG_CANDIDATE')])
    html = serve_page(monkeypatch, tmp_path, encode(project(artifact=artifact)), VIEW)
    assert 'Excluded</th><td>0' in html and 'Excluded symbols' not in html
    publish(tmp_path, b'{}')
    html = main.scanner_v2_egx_card_page().body.decode()
    assert 'REPORT_REJECTED' in html and 'Excluded symbols' not in html


def test_card_page_escapes_exclusion_values(tmp_path, monkeypatch):
    publish(tmp_path, encode(project()))
    monkeypatch.setenv(ENV, str(tmp_path))
    out = load_retained_egx_card_report(now=VIEW)
    coverage = out['report']['coverage']
    coverage['symbols'][-1]['reason'] = '<s>r</s>'
    coverage['identity_exclusions'][0]['reason'] = '<em>q</em>'
    html = render_card_report_page(out)
    assert '<s>' not in html and '&lt;s&gt;r' in html
    assert '<em>' not in html and '&lt;em&gt;q' in html


def test_card_page_escapes_every_supplied_value(tmp_path, monkeypatch):
    publish(tmp_path, encode(project()))
    monkeypatch.setenv(ENV, str(tmp_path))
    out = load_retained_egx_card_report(now=VIEW)
    card = out['report']['cards'][0]
    card['research_evidence']['signal_evidence_json'] = '<script>x</script>'
    card['observed_fact']['symbol'] = '<i>y</i>'
    out['report']['limitations'] = ['<b>z</b>']
    html = render_card_report_page(out)
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '<i>' not in html and '<b>' not in html
    failed = {'status': '<u>s</u>', 'reason': '<u>r</u>', 'detail': '<u>d</u>',
              'loaded_at': VIEW.isoformat(), 'report': None}
    assert '<u>' not in render_card_report_page(failed)
