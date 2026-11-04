"""Routing tests use disposable pointers and simulated HTTP; no sockets."""
import json
import os
from unittest.mock import Mock

import pytest

from tools.delivery_routing import ReportingRouter
from tools.delivery_runner import DeliveryError

A, B = 'a' * 40, 'b' * 40


@pytest.fixture
def router(tmp_path, monkeypatch):
    pointer = tmp_path / 'route.json'
    pointer.write_text(json.dumps(dict(commit=A, live_money=False)))
    pointer.chmod(0o644)
    r = ReportingRouter(pointer, {A: 8101, B: 8102}, {'/', '/health'},
        Mock(side_effect=lambda sha: dict(commit=sha, live_money=False, http=True,
                                         api=True, runtime_compatible=True)), owner=os.getuid())
    # /tmp is intentionally writable; only simulate protected ancestors, not file checks.
    original = type(pointer).stat
    def observed(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path in pointer.parents:
            return type('Info', (), dict(st_uid=os.getuid(), st_mode=0o40755))()
        return value
    monkeypatch.setattr(type(pointer), 'stat', observed)
    r._fetch = Mock(return_value=(b'report', 'text/html'))
    return r


def call(router, **changes):
    env = dict(REQUEST_METHOD='GET', PATH_INFO='/', QUERY_STRING='')
    env.update(changes)
    start = Mock()
    body = b''.join(router(env, start))
    return start.call_args.args[0], dict(start.call_args.args[1]), body


def test_cutover_and_rollback_follow_pointer(router):
    for sha in (A, B, A):
        router.pointer.write_text(json.dumps(dict(commit=sha, live_money=False)))
        status, headers, body = call(router)
        assert status == '200 OK' and body == b'report'
        assert headers['Cache-Control'] == 'no-store'
        router._fetch.assert_called_with(sha, '/')


@pytest.mark.parametrize('change', [dict(REQUEST_METHOD='POST'), dict(PATH_INFO='/admin'),
    dict(PATH_INFO='//evil'), dict(QUERY_STRING='target=evil'), dict(CONTENT_LENGTH='1'),
    dict(HTTP_TRANSFER_ENCODING='chunked')])
def test_request_cannot_select_target_or_mutate(router, change):
    assert call(router, **change)[0].startswith('503')
    router._fetch.assert_not_called()


@pytest.mark.parametrize('raw', ['{}', '[]', 'broken',
    json.dumps(dict(commit=A, live_money=True)), json.dumps(dict(commit='c'*40, live_money=False)),
    '{"commit":"' + A + '","live_money":true,"live_money":false}', ' ' * 4097])
def test_invalid_pointer_fails_closed(router, raw):
    router.pointer.write_text(raw)
    assert call(router)[0].startswith('503')
    router._fetch.assert_not_called()


def test_symlink_pointer_rejected(router):
    target = router.pointer.with_name('other')
    router.pointer.rename(target)
    router.pointer.symlink_to(target)
    assert call(router)[0].startswith('503')


def test_writable_pointer_rejected(router):
    router.pointer.chmod(0o666)
    assert call(router)[0].startswith('503')


@pytest.mark.parametrize('gate,value', [('commit', B), ('live_money', True), ('http', False),
                                       ('api', None), ('runtime_compatible', False)])
def test_service_gate_prevents_forwarding(router, gate, value):
    observation = router.verify_service(A)
    observation[gate] = value
    router.verify_service.side_effect = None
    router.verify_service.return_value = observation
    assert call(router)[0].startswith('503')
    router._fetch.assert_not_called()


def test_mid_request_cutover_discards_response(router):
    def fetch(*args):
        router.pointer.write_text(json.dumps(dict(commit=B, live_money=False)))
        return b'old response', 'text/plain'
    router._fetch.side_effect = fetch
    assert call(router)[2] == b'Release unavailable\n'


def test_upstream_failure_is_sanitized(router):
    router._fetch.side_effect = RuntimeError('private host details')
    assert call(router)[2] == b'Release unavailable\n'


def test_head_has_no_body(router):
    status, headers, body = call(router, REQUEST_METHOD='HEAD')
    assert status == '200 OK' and body == b'' and headers['Content-Length'] == '6'


@pytest.mark.parametrize('ports,paths', [({A: True}, {'/'}), ({A: 80}, {'/'}),
    ({A: 8101, B: 8101}, {'/'}), ({A: 8101}, {'//evil'}), ({A: 8101}, {'/../secret'}),
    ({A: 8101}, {'/%2fsecret'}), ({A: 8101}, {'/a?b'})])
def test_invalid_host_policy(tmp_path, ports, paths):
    with pytest.raises(DeliveryError):
        ReportingRouter(tmp_path / 'route', ports, paths, Mock())


@pytest.mark.parametrize('fault', [None, 'oversized', 'status', 'header', 'timeout'])
def test_http_transport_contract(router, monkeypatch, fault):
    response = Mock(status=200, headers={'Content-Type': 'application/json'})
    response.read1.side_effect = [b'{}', b'']
    if fault == 'oversized':
        response.read1.side_effect = None
        response.read1.return_value = b'x' * 65536
    elif fault == 'status':
        response.status = 302
    elif fault == 'header':
        response.headers['Content-Type'] = 'text/plain\r\nInjected: yes'
    elif fault == 'timeout':
        response.read1.side_effect = TimeoutError()
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = context
    build = Mock(return_value=opener)
    monkeypatch.setattr('tools.delivery_routing.build_opener', build)
    if fault:
        with pytest.raises((DeliveryError, TimeoutError)):
            ReportingRouter._fetch(router, A, '/health')
    else:
        assert ReportingRouter._fetch(router, A, '/health') == (b'{}', 'application/json')
    request = opener.open.call_args.args[0]
    assert request.full_url == 'http://127.0.0.1:8101/health'
    assert request.get_method() == 'GET'
    assert build.call_args.args[0].proxies == {}


def test_service_drift_after_fetch_is_rejected(router):
    good = router.verify_service(A)
    router.verify_service.side_effect = [good, {**good, 'live_money': True}]
    assert call(router)[0].startswith('503')


def test_unprotected_ancestor_rejected(router, monkeypatch):
    original = type(router.pointer).stat
    def changed(path, *args, **kwargs):
        if path == router.pointer.parent:
            return type('Info', (), dict(st_uid=os.getuid(), st_mode=0o40777))()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(router.pointer), 'stat', changed)
    assert call(router)[0].startswith('503')
    router._fetch.assert_not_called()
