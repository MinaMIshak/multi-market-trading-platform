"""Read-only Scanner v2 card-report route over the protected loader.

Artificial retained reports in temporary directories only. No producer, market
coverage, admission, fill or edge evidence is implied.
"""
import json

import app.main as main
from app.scanner_v2_card_report import API_ROUTE, feature_status
from app.scanner_v2_card_report_loader import (
    ENV, RESPONSE_SCHEMA, load_retained_egx_card_report)
from tests.test_scanner_v2_card_report import VIEW, project
from tests.test_scanner_v2_card_report_loader import (
    SAFETY, assert_unavailable, encode, publish)


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
