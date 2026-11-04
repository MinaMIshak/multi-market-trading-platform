"""Real disposable cron files; no installed daemon or host cron claims."""
from unittest.mock import Mock

import pytest

from tools.delivery_runner import DeliveryError
from tools.delivery_schedule import CronJob, ReleaseSchedule

OLD, NEW = 'b' * 40, 'a' * 40


@pytest.fixture
def schedule(tmp_path):
    env, bundles, cron = [tmp_path / n for n in ('env', 'bundles', 'cron')]
    for path in (env, bundles, cron):
        path.mkdir()
    for sha in (OLD, NEW):
        python = env / sha / 'venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('fixture interpreter, never executed')
        python.chmod(0o755)
        (bundles / sha / 'source').mkdir(parents=True)
    target = cron / 'egx-delivery'
    result = ReleaseSchedule(target, env, bundles, 'egx',
                             [CronJob(15, 4, 'tools.nightly')], Mock(return_value=True))
    target.write_text(result.release_schedule(OLD))
    target.chmod(0o644)
    return result


def test_install_and_restart_rollback(schedule):
    old = schedule.read_schedule()
    new = schedule.release_schedule(NEW)
    schedule.install_schedule(new)
    assert schedule.read_schedule() == new
    fresh = ReleaseSchedule(schedule.target, schedule.environments, schedule.bundles,
                            schedule.user, schedule.jobs, schedule.verify_release)
    for _ in range(2):
        fresh.install_schedule(old)
        assert fresh.verify_schedule(OLD, fresh.read_schedule())
    assert list(schedule.target.parent.iterdir()) == [schedule.target]
    assert schedule.target.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize('mutation', [lambda t: t.replace('false', 'true'),
    lambda t: t + '* * * * * root malicious\n',
    lambda t: t.replace('/venv/bin/python', '/worktree/python'),
    lambda t: t.replace(NEW, OLD, 1), lambda t: '', lambda t: t.replace('15 4', '* *')])
def test_unapproved_schedule_preserves_previous(schedule, mutation):
    old = schedule.read_schedule()
    with pytest.raises(DeliveryError):
        schedule.install_schedule(mutation(schedule.release_schedule(NEW)))
    assert schedule.read_schedule() == old


@pytest.mark.parametrize('value', [False, None, 1])
def test_release_verification_strict(schedule, value):
    schedule.verify_release.return_value = value
    with pytest.raises(DeliveryError):
        schedule.release_schedule(NEW)


@pytest.mark.parametrize('kind', ['missing', 'symlink', 'writable', 'directory'])
def test_unsafe_existing_state(schedule, kind):
    if kind == 'writable':
        schedule.target.chmod(0o666)
    else:
        schedule.target.unlink()
        if kind == 'symlink':
            schedule.target.symlink_to(schedule.bundles)
        elif kind == 'directory':
            schedule.target.mkdir()
    with pytest.raises((DeliveryError, OSError)):
        schedule.install_schedule(schedule.release_schedule(NEW))


def test_atomic_replace_failure_preserves_previous(schedule, monkeypatch):
    old = schedule.read_schedule()
    monkeypatch.setattr('tools.delivery_schedule.os.replace', Mock(side_effect=OSError('disk')))
    with pytest.raises(OSError):
        schedule.install_schedule(schedule.release_schedule(NEW))
    assert schedule.read_schedule() == old
    assert list(schedule.target.parent.iterdir()) == [schedule.target]


def test_readback_failure_surfaces_and_can_restore(schedule, monkeypatch):
    old = schedule.read_schedule()
    real = schedule.read_schedule
    monkeypatch.setattr(schedule, 'read_schedule', Mock(side_effect=[old, 'wrong']))
    with pytest.raises(DeliveryError, match='readback'):
        schedule.install_schedule(schedule.release_schedule(NEW))
    monkeypatch.setattr(schedule, 'read_schedule', real)
    schedule.install_schedule(old)
    assert real() == old


@pytest.mark.parametrize('job', [CronJob(True, 4, 'x'), CronJob(60, 4, 'x'),
                                CronJob(0, 24, 'x'), CronJob(0, 4, 'x;evil')])
def test_invalid_policy(schedule, job):
    with pytest.raises(DeliveryError):
        ReleaseSchedule(schedule.target, schedule.environments, schedule.bundles,
                        'egx', [job], schedule.verify_release)


def test_release_local_command_and_clean_environment(schedule):
    text = schedule.release_schedule(NEW)
    assert f'{schedule.environments}/{NEW}/venv/bin/python -m tools.nightly' in text
    assert '/usr/bin/env -i PATH=/usr/bin:/bin LIVE_MONEY=false' in text
    assert not schedule.verify_schedule(OLD, text)


def test_production_transaction_schedule_failure_rolls_back(schedule, tmp_path):
    from tools.delivery_production import ProductionBackend
    from tools.delivery_runner import atomic_json
    state, routing = tmp_path / 'state', tmp_path / 'routing'
    state.mkdir()
    routing.mkdir()
    route = routing / 'current.json'
    atomic_json(route, dict(commit=OLD, live_money=False))
    services = Mock()
    services.contained.return_value = True
    services.stage.side_effect = services.observe.side_effect = lambda sha: dict(
        commit=sha, live_money=False, http=True, api=True, runtime_compatible=True, sig_v1=True)
    for name in ('read_schedule', 'release_schedule', 'verify_schedule', 'install_schedule'):
        setattr(services, name, getattr(schedule, name))
    backend = ProductionBackend(state, route, services)
    snapshot = backend.snapshot()
    backend.cutover(NEW)
    def partial(text):
        schedule.install_schedule(text)
        raise OSError('host interrupted after installation')
    services.install_schedule = partial
    with pytest.raises(OSError):
        backend.adopt_schedule(NEW)
    assert schedule.verify_schedule(NEW, schedule.read_schedule())
    services.install_schedule = schedule.install_schedule
    restarted = ProductionBackend(state, route, services)
    restarted.restore_release(snapshot)
    restarted.restore_schedule(snapshot)
    assert restarted.rollback_check(snapshot)['scheduler_target'] == OLD
    assert schedule.read_schedule() == snapshot['schedule']
