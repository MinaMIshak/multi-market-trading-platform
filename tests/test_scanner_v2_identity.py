"""Synthetic identity snapshots; no admission or current coverage evidence."""
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid5

import pytest

from app.data.models import RawArtifactManifest
from app.scanner_v2_identity import project_egid_identities

SESSION = date(2026, 11, 3)
AT = datetime(2026, 11, 3, 16, tzinfo=timezone.utc)
ROWS = [{'SYMBOL_CODE': 'EG0001', 'Reuters': 'AAAA.CA'},
        {'SYMBOL_CODE': 'EGX30'}, {'SYMBOL_CODE': 'UNKNOWN'}]


def project(rows=ROWS, *, payload=None, changes=None, **kwargs):
    payload = json.dumps(rows).encode() if payload is None else payload
    fields = dict(provider='egid', asset_type='SECURITY_MASTER', market_date=SESSION,
                  raw_path='unused/no-file-is-read', sha256=sha256(payload).hexdigest(),
                  byte_size=len(payload), record_count=len(rows), received_at=AT)
    fields.update(changes or {})
    args = dict(session=SESSION, observed_at=AT, requested=('UNKNOWN', 'MISSING', 'EGX30', 'AAAA'))
    args.update(kwargs)
    return project_egid_identities(payload, manifest=RawArtifactManifest(**fields), **args)


def test_exact_immutable_deterministic_identity_and_visible_exclusions():
    result = project()
    assert result == project(requested=('AAAA', 'EGX30', 'MISSING', 'UNKNOWN'))
    assert result.instrument_ids == (('AAAA', uuid5(NAMESPACE_URL, 'egx://instrument/egid/EG0001')),)
    assert result.exclusions == (('EGX30', 'NOT_EQUITY'), ('MISSING', 'IDENTITY_MISSING'),
                                 ('UNKNOWN', 'NOT_EQUITY'))
    assert result.admission == 'NOT_ESTABLISHED' and result.live_money is False
    assert result.artifact_sha256 in result.evidence_id
    with pytest.raises(ValueError):
        result.market = 'US'
    assert json.loads(result.model_dump_json())['market'] == 'EGX'


@pytest.mark.parametrize('changes', [
    {'provider': 'us'}, {'asset_type': 'DAILY_BARS'}, {'granularity': 'D1'},
    {'symbol': 'AAAA'}, {'market_date': date(2026, 11, 2)}, {'sha256': '0' * 64},
    {'byte_size': 1}, {'record_count': 1},
    {'received_at': datetime(2026, 11, 4, 16, tzinfo=timezone.utc)},
    {'received_at': datetime(2026, 11, 2, 16, tzinfo=timezone.utc)},
])
def test_manifest_source_integrity_and_pit(changes):
    with pytest.raises(ValueError):
        project(changes=changes)


@pytest.mark.parametrize('requested', [('aaaa',), ('AAAA', 'AAAA'), ('AA\nAA',),
                                        ('',), (1,), ['AAAA'], ('A' * 65,)])
def test_scope_never_guesses_or_coerces(requested):
    with pytest.raises(ValueError):
        project(requested=requested)


@pytest.mark.parametrize('rows', [[None], [{'SYMBOL_CODE': 1}],
    [{'SYMBOL_CODE': 'X', 'Reuters': 1}],
    [{'SYMBOL_CODE': 'X', 'Reuters': 'AAAA.CA'}, {'SYMBOL_CODE': 'Y', 'Reuters': 'AAAA.CA'}],
    [{'SYMBOL_CODE': 'X'}, {'SYMBOL_CODE': 'X'}]])
def test_bad_or_ambiguous_records_reject_whole_snapshot(rows):
    with pytest.raises(ValueError):
        project(rows)


@pytest.mark.parametrize('payload', [b'[{"SYMBOL_CODE":"X","SYMBOL_CODE":"Y"}]',
    b'[{"SYMBOL_CODE":"X","unused":NaN}]', b'[{"SYMBOL_CODE":"X","unused":1e999}]', b'{}'])
def test_strict_json_even_for_unrequested_records(payload):
    with pytest.raises(ValueError):
        project(payload=payload)


def test_time_requires_aware_observation_and_no_future_session():
    for at in (AT.replace(tzinfo=None), datetime(2026, 11, 2, 16, tzinfo=timezone.utc)):
        with pytest.raises(ValueError):
            project(observed_at=at)


def test_empty_snapshot_is_missing_not_admitted():
    result = project([])
    assert not result.instrument_ids
    assert all(reason == 'IDENTITY_MISSING' for _, reason in result.exclusions)
    assert result.admission == 'NOT_ESTABLISHED'


def test_bounds(monkeypatch):
    import app.scanner_v2_identity as module
    for constant in ('MAX_BYTES', 'MAX_RECORDS', 'MAX_REQUESTED'):
        with monkeypatch.context() as m:
            m.setattr(module, constant, 1)
            with pytest.raises(ValueError):
                project()
