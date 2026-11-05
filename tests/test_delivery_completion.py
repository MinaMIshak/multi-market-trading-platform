"""Artificial completion callbacks; no installed-host acceptance."""
import hashlib
import json
from unittest.mock import patch

import pytest

from tests.test_delivery_publication import setup
from tests.test_delivery_observations import NOW
from tests.test_delivery_status import HEAD
from tools.delivery_observations import record_completion
from tools.delivery_runner import DeliveryError, delivery_lock


def envelope(setup, **changes):
    event = dict(schema='delivery-completion-v1', kind='latest_codex_cycle',
                 event_id='cycle-1', source_head=HEAD, status='SUCCEEDED',
                 observed_at=NOW.isoformat(), live_money=False, evidence='artificial exit=0')
    event.update(changes)
    path = setup[0].state / 'completion.json'
    path.write_text(json.dumps(event))
    path.chmod(0o600)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('kind,status', [('latest_codex_cycle', 'INTERRUPTED'),
    ('latest_claude_review', 'COMPLETED'), ('egx_chain_status', 'FAILED'),
    ('us_chain_status', 'COMPLETED')])
def test_callback_retains_exact_envelope_and_replays(setup, kind, status):
    path, digest = envelope(setup, kind=kind, status=status)
    result = record_completion(setup[0], path, digest)
    assert result['observations'][kind]['evidence_sha256'] == digest
    assert record_completion(setup[0], path, digest) == result
    receipt = json.loads(next(setup[0].state.glob('observation-*.json')).read_bytes())
    assert bytes.fromhex(receipt['evidence_hex']) == path.read_bytes()


@pytest.mark.parametrize('changes', [dict(schema='bad'), dict(command=['sh']),
    dict(evidence=' '), dict(evidence={}), dict(live_money=True),
    dict(kind='tests'), dict(kind=[]), dict(source_head='bad'), dict(status='GREEN')])
def test_invalid_callback_cannot_advance(setup, changes):
    with pytest.raises(DeliveryError):
        record_completion(setup[0], *envelope(setup, **changes))
    assert not (setup[0].state / 'observations.json').exists()


@pytest.mark.parametrize('raw', [b'{', b'[]', b'{"schema":1,"schema":2}', b'\xff'])
def test_malformed_envelope(setup, raw):
    path, _ = envelope(setup)
    path.write_bytes(raw)
    with pytest.raises(DeliveryError):
        record_completion(setup[0], path, hashlib.sha256(raw).hexdigest())


def test_pin_checked_again_under_lease(setup):
    path, digest = envelope(setup)
    from tools.delivery_observations import protected_bytes
    def mutate_after_read(*args):
        raw = protected_bytes(*args)
        path.write_bytes(raw + b' ')
        return raw
    with patch('tools.delivery_observations.protected_bytes', side_effect=mutate_after_read):
        with pytest.raises(DeliveryError, match='pin mismatch'):
            record_completion(setup[0], path, digest)
    assert not list(setup[0].state.glob('observation-*.json'))


def test_concurrent_callback_and_wrong_pin(setup):
    path, digest = envelope(setup)
    with pytest.raises(DeliveryError, match='pin mismatch'):
        record_completion(setup[0], path, '0' * 64)
    with delivery_lock(setup[0].state), pytest.raises(DeliveryError):
        record_completion(setup[0], path, digest)
