"""Offline trusted-transport tests; no keys, network or production access."""
import hashlib
from unittest.mock import Mock

import pytest

from tools.delivery_runner import BRANCH, DeliveryError
from tools.delivery_source import PinnedGitSource

HEAD = 'a' * 40
ORIGIN = 'git@github-multimarket:owner/repo.git'
CONFIG = b'Host github-multimarket\n HostName github.com\n User git\n IdentityFile /host/key\n IdentitiesOnly yes\n'
RESOLVED = ('hostname github.com\nuser git\nidentityfile /host/key\n'
            'identitiesonly yes\nstricthostkeychecking true\nbatchmode yes\n')


@pytest.fixture
def source(tmp_path):
    config = tmp_path / 'ssh_config'
    config.write_bytes(CONFIG)
    return PinnedGitSource(tmp_path, origin=ORIGIN, ssh_config=config,
                           config_sha256=hashlib.sha256(CONFIG).hexdigest(),
                           identity_file='/host/key')


def fake_git(source, overrides=None):
    replies = {
        ('symbolic-ref', '--short', 'HEAD'): BRANCH,
        ('status', '--porcelain', '--untracked-files=all'): '',
        ('rev-parse', 'HEAD'): HEAD,
        ('remote', 'get-url', '--all', 'origin'): ORIGIN,
        ('remote', 'get-url', '--push', '--all', 'origin'): ORIGIN,
        ('push', 'origin', f'{HEAD}:refs/heads/{BRANCH}'): '',
        ('ls-remote', '--exit-code', 'origin', f'refs/heads/{BRANCH}'):
            f'{HEAD}\trefs/heads/{BRANCH}',
    }
    replies.update(overrides or {})
    source.git = Mock(side_effect=lambda *args: replies[args])


def test_push_checks_identity_before_and_after_exact_push(source, monkeypatch):
    fake_git(source)
    ssh = Mock(return_value=RESOLVED)
    monkeypatch.setattr('tools.delivery_source.subprocess.check_output', ssh)
    source.push(HEAD)
    assert ssh.call_count == 2
    source.git.assert_any_call('push', 'origin', f'{HEAD}:refs/heads/{BRANCH}')
    source.git.assert_any_call('ls-remote', '--exit-code', 'origin', f'refs/heads/{BRANCH}')


@pytest.mark.parametrize('key,value', [
    ('hostname github.com', 'hostname attacker.invalid'),
    ('user git', 'user root'),
    ('identityfile /host/key', 'identityfile /other/key'),
    ('identitiesonly yes', 'identitiesonly no'),
    ('stricthostkeychecking true', 'stricthostkeychecking false'),
    ('batchmode yes', 'batchmode no'),
    ('identityfile /host/key', 'identityfile /host/key\nidentityfile /other/key'),
    ('hostname github.com', ''),
])
def test_resolved_identity_rejects_wrong_or_ambiguous_config(source, monkeypatch, key, value):
    fake_git(source)
    monkeypatch.setattr('tools.delivery_source.subprocess.check_output',
                        Mock(return_value=RESOLVED.replace(key, value)))
    with pytest.raises(DeliveryError):
        source.push(HEAD)
    assert not any(c.args[0] == 'push' for c in source.git.call_args_list)


@pytest.mark.parametrize('extra', ['proxycommand evil', 'proxyjump other',
                                 'localcommand evil', 'permitlocalcommand yes'])
def test_command_indirection_rejected(source, monkeypatch, extra):
    monkeypatch.setattr('tools.delivery_source.subprocess.check_output',
                        Mock(return_value=RESOLVED + extra + '\n'))
    with pytest.raises(DeliveryError):
        source._check_identity()


@pytest.mark.parametrize('line', ['Include /other', 'Include=/other',
                                 'Match exec evil', '  MATCH=exec evil'])
def test_dynamic_config_never_executes_ssh(source, monkeypatch, line):
    content = CONFIG + line.encode() + b'\n'
    source.ssh_config.write_bytes(content)
    source.config_sha256 = hashlib.sha256(content).hexdigest()
    runner = Mock()
    monkeypatch.setattr('tools.delivery_source.subprocess.check_output', runner)
    with pytest.raises(DeliveryError):
        source._check_identity()
    runner.assert_not_called()


def test_config_change_rejected_before_command(source, monkeypatch):
    source.ssh_config.write_bytes(CONFIG + b'# changed\n')
    runner = Mock()
    monkeypatch.setattr('tools.delivery_source.subprocess.check_output', runner)
    with pytest.raises(DeliveryError, match='digest'):
        source.git('status')
    runner.assert_not_called()


def test_symlink_config_rejected(source, tmp_path):
    link = tmp_path / 'link'
    link.symlink_to(source.ssh_config)
    source.ssh_config = link
    with pytest.raises(DeliveryError, match='path'):
        source._transport()


def test_wrong_repository_under_correct_alias_rejected(source):
    fake_git(source, {('remote', 'get-url', '--all', 'origin'):
                     'git@github-multimarket:other/repo.git',
                     ('remote', 'get-url', '--push', '--all', 'origin'):
                     'git@github-multimarket:other/repo.git'})
    source._check_identity = Mock()
    with pytest.raises(DeliveryError, match='pinned origin'):
        source.push(HEAD)
    source._check_identity.assert_not_called()


def test_transport_sanitizes_environment_and_disables_hooks(source, monkeypatch):
    for name in ('GIT_CONFIG_COUNT', 'GIT_SSH_COMMAND', 'SSH_AUTH_SOCK', 'GIT_DIR'):
        monkeypatch.setenv(name, 'untrusted')
    runner = Mock(return_value='output\n')
    monkeypatch.setattr('tools.delivery_source.subprocess.check_output', runner)
    assert source.git('status') == 'output'
    args, kwargs = runner.call_args
    assert args[0][0] == '/usr/bin/git'
    assert 'core.hooksPath=/dev/null' in args[0]
    assert 'protocol.allow=never' in args[0]
    env = kwargs['env']
    assert not {'GIT_CONFIG_COUNT', 'SSH_AUTH_SOCK', 'GIT_DIR'} & env.keys()
    assert '-oIdentityAgent=none' in env['GIT_SSH_COMMAND']
    assert '-oControlPath=none' in env['GIT_SSH_COMMAND']
    assert '-oStrictHostKeyChecking=yes' in env['GIT_SSH_COMMAND']


def test_remote_mismatch_after_push_fails_closed(source, monkeypatch):
    fake_git(source, {('ls-remote', '--exit-code', 'origin', f'refs/heads/{BRANCH}'):
                     f'{"b" * 40}\trefs/heads/{BRANCH}'})
    monkeypatch.setattr('tools.delivery_source.subprocess.check_output',
                        Mock(return_value=RESOLVED))
    with pytest.raises(DeliveryError, match='origin mismatch'):
        source.push(HEAD)


@pytest.mark.parametrize('command,value', [
    (('symbolic-ref', '--short', 'HEAD'), 'main'),
    (('status', '--porcelain', '--untracked-files=all'), '?? dirty'),
    (('rev-parse', 'HEAD'), 'b' * 40),
])
def test_pinned_source_preserves_source_guards(source, command, value):
    fake_git(source, {command: value})
    with pytest.raises(DeliveryError):
        source.push(HEAD)
    assert not any(c.args[0] == 'push' for c in source.git.call_args_list)
