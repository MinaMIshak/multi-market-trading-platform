"""Transaction fault injection; no production/network operations."""
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock

import pytest

from tools.delivery_runner import (BRANCH, SOCKET_TESTS, DeliveryError, GitSource,
                                   acceptance, deliver, delivery_lock)

HEAD = 'a' * 40
OLD = 'b' * 40
NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def evidence(sha=HEAD):
    return dict(commit=sha, live_money=False, http=True, api=True,
                runtime_compatible=True, sig_v1=True,
                scheduler_target=sha, scheduler_verified=True)


@pytest.fixture
def setup(tmp_path):
    request = dict(schema='delivery-request-v1', request_id='cycle-1', head=HEAD,
                   created_at=NOW.isoformat(), live_money=False, scope='SIG-v1-reporting-only')
    source = Mock()
    host = Mock()
    host.validate.return_value = dict(commit=HEAD, full_suite=True, reporting_only=True,
                                     runtime_rss=True, socket_tests={t: True for t in SOCKET_TESTS})
    host.candidate.return_value = evidence()
    host.production.return_value = evidence()
    host.snapshot.return_value = dict(commit=OLD, verified=True, schedule='exact prior cron bytes')
    host.rollback_check.return_value = evidence(OLD)
    return request, tmp_path, source, host


def run(setup):
    return deliver(*setup, now=NOW)


def status(setup):
    return json.loads((setup[1] / 'delivery.json').read_text())


def test_success_order_and_durable_rollback_before_cutover(setup):
    request, state, source, host = setup
    def cutover(sha):
        assert status(setup)['status'] == 'CUTOVER_PENDING'
        assert status(setup)['rollback'] == host.snapshot.return_value
    host.cutover.side_effect = cutover
    result = run(setup)
    assert result['status'] == 'SUCCEEDED'
    assert result['deployed_release'] == HEAD
    assert [c[0] for c in host.method_calls] == [
        'prepare', 'validate', 'candidate', 'snapshot', 'cutover', 'adopt_schedule',
        'production', 'cleanup']
    assert source.check.call_count == 2
    source.push.assert_called_once_with(HEAD)
    assert status(setup) == json.loads((state / 'cycle-1.json').read_text())


@pytest.mark.parametrize('offset', [-901, 1])
def test_stale_or_future_request(setup, offset):
    setup[0]['created_at'] = (NOW + timedelta(seconds=offset)).isoformat()
    with pytest.raises(DeliveryError, match='stale or future'):
        run(setup)
    setup[2].check.assert_not_called()


@pytest.mark.parametrize('reason', ['dirty tree', 'wrong branch', 'source HEAD mismatch'])
def test_source_rejection_prevents_push_and_release(setup, reason):
    setup[2].check.side_effect = DeliveryError(reason)
    with pytest.raises(DeliveryError, match=reason):
        run(setup)
    setup[2].push.assert_not_called()
    setup[3].prepare.assert_not_called()
    assert status(setup)['status'] == 'FAILED'


def test_origin_mismatch_prevents_release(setup):
    setup[2].push.side_effect = DeliveryError('origin mismatch')
    with pytest.raises(DeliveryError, match='origin mismatch'):
        run(setup)
    setup[3].prepare.assert_not_called()


@pytest.mark.parametrize('key', ['full_suite', 'reporting_only', 'runtime_rss', 'socket_tests', 'commit'])
def test_validation_cannot_be_skipped(setup, key):
    setup[3].validate.return_value.pop(key)
    with pytest.raises(DeliveryError, match='host validation'):
        run(setup)
    setup[3].candidate.assert_not_called()
    setup[3].cutover.assert_not_called()


@pytest.mark.parametrize('key,value', [('http', False), ('api', False), ('sig_v1', None),
    ('runtime_compatible', False), ('commit', OLD), ('live_money', True), ('live_money', 0)])
def test_candidate_failure_preserves_production(setup, key, value):
    setup[3].candidate.return_value[key] = value
    with pytest.raises(DeliveryError):
        run(setup)
    setup[3].cutover.assert_not_called()
    setup[3].adopt_schedule.assert_not_called()
    assert status(setup)['status'] == 'FAILED'


@pytest.mark.parametrize('operation', ['cutover', 'adopt_schedule', 'production'])
def test_partial_mutation_rolls_back_release_and_schedule(setup, operation):
    host = setup[3]
    getattr(host, operation).side_effect = RuntimeError('partial failure')
    with pytest.raises(DeliveryError, match='ROLLED_BACK'):
        run(setup)
    host.restore_release.assert_called_once_with(host.snapshot.return_value)
    host.restore_schedule.assert_called_once_with(host.snapshot.return_value)
    assert status(setup)['deployed_release'] == OLD
    assert status(setup)['scheduler_target'] == OLD


@pytest.mark.parametrize('key,value', [('http', False), ('live_money', True),
                                     ('scheduler_target', OLD), ('scheduler_verified', False)])
def test_post_cutover_gate_failure_rolls_back(setup, key, value):
    setup[3].production.return_value[key] = value
    with pytest.raises(DeliveryError, match='ROLLED_BACK'):
        run(setup)
    setup[3].restore_schedule.assert_called_once()


@pytest.mark.parametrize('operation', ['restore_release', 'restore_schedule', 'rollback_check'])
def test_failed_rollback_blocks_next_delivery(setup, operation):
    host = setup[3]
    host.production.side_effect = RuntimeError('bad production')
    getattr(host, operation).side_effect = RuntimeError('bad restore')
    with pytest.raises(DeliveryError, match='RECOVERY_REQUIRED'):
        run(setup)
    host.restore_release.assert_called_once()
    host.restore_schedule.assert_called_once()
    setup[0]['request_id'] = 'cycle-2'
    with pytest.raises(DeliveryError, match='unresolved'):
        run(setup)


def test_concurrent_release_rejected(setup):
    with delivery_lock(setup[1]):
        with pytest.raises(DeliveryError, match='concurrent'):
            run(setup)
    setup[2].check.assert_not_called()


def test_replayed_request_rejected(setup):
    run(setup)
    with pytest.raises(DeliveryError, match='replayed'):
        run(setup)


def test_interruption_requires_recovery(setup):
    setup[3].cutover.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run(setup)
    assert status(setup)['status'] == 'CUTOVER_PENDING'
    setup[0]['request_id'] = 'cycle-2'
    with pytest.raises(DeliveryError, match='unresolved'):
        run(setup)


def test_head_changed_during_validation_prevents_cutover(setup):
    setup[2].check.side_effect = [None, DeliveryError('source HEAD mismatch')]
    with pytest.raises(DeliveryError, match='source HEAD mismatch'):
        run(setup)
    setup[3].cutover.assert_not_called()


def git_source(overrides=None):
    source = GitSource('/unused')
    replies = {
        ('symbolic-ref', '--short', 'HEAD'): BRANCH,
        ('status', '--porcelain', '--untracked-files=all'): '',
        ('rev-parse', 'HEAD'): HEAD,
        ('remote', 'get-url', '--all', 'origin'): 'git@github-multimarket:owner/repo.git',
        ('remote', 'get-url', '--push', '--all', 'origin'): 'git@github-multimarket:owner/repo.git',
        ('push', 'origin', f'{HEAD}:refs/heads/{BRANCH}'): '',
        ('ls-remote', '--exit-code', 'origin', f'refs/heads/{BRANCH}'): f'{HEAD}\trefs/heads/{BRANCH}',
    }
    replies.update(overrides or {})
    source.git = Mock(side_effect=lambda *args: replies[args])
    return source


def test_git_push_exact_sha_and_remote_verification():
    source = git_source()
    source.push(HEAD)
    source.git.assert_any_call('push', 'origin', f'{HEAD}:refs/heads/{BRANCH}')
    source.git.assert_any_call('ls-remote', '--exit-code', 'origin', f'refs/heads/{BRANCH}')


@pytest.mark.parametrize('command,value', [
    (('symbolic-ref', '--short', 'HEAD'), 'main'),
    (('status', '--porcelain', '--untracked-files=all'), '?? untracked'),
    (('rev-parse', 'HEAD'), OLD),
    (('remote', 'get-url', '--all', 'origin'), 'git@github.com:owner/repo.git'),
    (('remote', 'get-url', '--push', '--all', 'origin'), 'https://example.invalid/repo'),
    (('ls-remote', '--exit-code', 'origin', f'refs/heads/{BRANCH}'), f'{OLD}\trefs/heads/{BRANCH}'),
])
def test_git_guards(command, value):
    with pytest.raises(DeliveryError):
        git_source({command: value}).push(HEAD)


@pytest.mark.parametrize('value', [1, 'passed', None, False])
def test_socket_results_require_explicit_boolean_success(setup, value):
    setup[3].validate.return_value['socket_tests'][SOCKET_TESTS[0]] = value
    with pytest.raises(DeliveryError, match='host validation'):
        run(setup)
    setup[3].cutover.assert_not_called()


@pytest.mark.parametrize('key,value', [('live_money', True), ('live_money', 0),
    ('head', 'a' * 7), ('scope', 'promote-champion'), ('created_at', '2026-11-04'),
    ('request_id', '../escape'), ('command', 'arbitrary')])
def test_invalid_request_cannot_invoke_host(setup, key, value):
    setup[0][key] = value
    with pytest.raises(DeliveryError):
        run(setup)
    setup[2].check.assert_not_called()
    setup[3].prepare.assert_not_called()


def test_missing_schedule_backup_prevents_cutover(setup):
    setup[3].snapshot.return_value['schedule'] = None
    with pytest.raises(DeliveryError, match='rollback state'):
        run(setup)
    setup[3].cutover.assert_not_called()


def test_rollback_accepts_verified_previous_release_without_sig_v1(setup):
    setup[3].production.return_value['http'] = False
    setup[3].rollback_check.return_value.pop('sig_v1')
    with pytest.raises(DeliveryError, match='ROLLED_BACK'):
        run(setup)
    assert status(setup)['deployed_release'] == OLD
