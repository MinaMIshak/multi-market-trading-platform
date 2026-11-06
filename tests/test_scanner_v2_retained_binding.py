"""Artificial retained evidence only; no admission, market coverage or edge evidence."""
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from uuid import UUID

import pytest

from app.scanner_v2_calendar import CalendarProjection, project_egx_calendar
from app.scanner_v2_identity import IdentityProjection, ObservedIdentityProjection
from app.scanner_v2_retained_binding import (
    FRESHNESS_POLICY, RetainedEgxRankBinding, bind_retained_egx_rank_report,
    compose_egx_rank_binding)
from app.strategies.trade_decision import trade_card
from tests.test_scanner_v2_egx_rank import CLOSE, GENERATED, NEXT_CLOSE, report

UTC = timezone.utc
SESSION = date(2026, 11, 3)
OPEN = datetime(2026, 11, 4, 8, tzinfo=UTC)                 # 10:00 Africa/Cairo
RAW, RECEIPT = 'a' * 64, 'b' * 64


def calendar(observed_at=datetime(2026, 11, 4, 5, tzinfo=UTC)):
    rows = [
        dict(market_date='2026-11-03', status='VERIFIED', opened_at='2026-11-03T10:00:00+02:00',
             closed_at='2026-11-03T14:30:00+02:00', data_verified_at='2026-11-03T15:00:00+02:00'),
        dict(market_date='2026-11-04', status='UNKNOWN', opened_at='2026-11-04T10:00:00+02:00',
             closed_at='2026-11-04T14:30:00+02:00'),
    ]
    payload = json.dumps(rows).encode()
    return project_egx_calendar(payload, expected_sha256=sha256(payload).hexdigest(),
                                session=SESSION, observed_at=observed_at,
                                decision_time=observed_at)


def identity(session=SESSION, available_at=datetime(2026, 11, 3, 18, tzinfo=UTC),
             received_at=datetime(2026, 11, 3, 17, tzinfo=UTC),
             observed_at=datetime(2026, 11, 4, 5, 30, tzinfo=UTC)):
    raw = IdentityProjection(
        session=session, observed_at=observed_at, received_at=received_at,
        artifact_sha256=RAW, instrument_ids=(('AAAA', UUID(int=1)), ('BBBB', UUID(int=2))),
        exclusions=(('EEEE', 'IDENTITY_MISSING'),),
        evidence_id=f'egid-security-master-sha256:{RAW}')
    return ObservedIdentityProjection(identity=raw, available_at=available_at,
                                      receipt_sha256=RECEIPT)


def bind(evidence=None, artifact=None):
    artifact = artifact if artifact is not None else report()
    return bind_retained_egx_rank_report(
        artifact, expected_sha256=sha256(artifact).hexdigest(),
        evidence=evidence or compose_egx_rank_binding(identity=identity(), calendar=calendar()))


def test_composes_binding_from_retained_evidence_without_upgrading_admission():
    cal = calendar()
    evidence = compose_egx_rank_binding(identity=identity(), calendar=cal)
    b = evidence.binding
    assert (b.session, b.next_session) == (SESSION, date(2026, 11, 4))
    assert b.data_as_of == CLOSE and b.valid_until == NEXT_CLOSE
    assert b.next_session_open == b.fresh_until == OPEN
    assert b.instrument_ids == (('AAAA', UUID(int=1)), ('BBBB', UUID(int=2)))
    assert b.calendar_evidence_id == cal.evidence_id
    assert b.identity_evidence_id == f'egid-admission-receipt-sha256:{RECEIPT}'
    assert evidence.evidence_available_at == datetime(2026, 11, 4, 5, tzinfo=UTC)
    assert evidence.freshness_policy == FRESHNESS_POLICY
    assert evidence.identity_admission == 'INTERNAL_SNAPSHOT_VERIFIED'
    assert evidence.calendar_admission == evidence.calendar.admission == 'NOT_ESTABLISHED'
    assert evidence.identity.identity.admission == 'NOT_ESTABLISHED'
    assert evidence.runtime_activation is False and evidence.live_money is False
    assert compose_egx_rank_binding(identity=identity(), calendar=cal) == evidence
    with pytest.raises(ValueError):
        evidence.live_money = True


def test_retained_binding_feeds_existing_rank_adapter_and_card():
    result = bind()
    assert result['candidate_count'] == 2
    assert [o['reason'] for o in result['symbols']] == [
        None, None, 'SETUP_NOT_CONFIRMED', 'NO_TRADE', 'IDENTITY_UNBOUND']
    assert result['identity_exclusions'] == [{'symbol': 'EEEE', 'reason': 'IDENTITY_MISSING'}]
    assert result['calendar_admission'] == 'NOT_ESTABLISHED'
    assert result['promotion'] == 'NOT_AUTHORIZED' and result['risk_status'] == 'NOT_EVALUATED'
    assert result['live_money'] is result['execution_allowed'] is result['runtime_activation'] is False
    json.dumps(result['retained_evidence'])
    decision = result['decisions'][0]
    assert decision.source.fresh_until == OPEN
    assert f'egid-admission-receipt-sha256:{RECEIPT}' in decision.source.evidence_ids
    assert calendar().evidence_id in decision.source.evidence_ids
    generated = datetime.fromisoformat(GENERATED)
    assert trade_card(decision, observed_at=generated)['status'] == 'RESEARCH_CANDIDATE'
    stale = trade_card(decision, observed_at=OPEN)
    assert stale['status'] == 'EXCLUDED' and stale['invalidation_reason'] == 'DATA_STALE'


@pytest.mark.parametrize('kwargs', [
    {'available_at': datetime(2026, 11, 4, 6, 50, tzinfo=UTC),
     'observed_at': datetime(2026, 11, 4, 7, tzinfo=UTC)},
    {'received_at': datetime(2026, 11, 4, 6, 50, tzinfo=UTC),
     'observed_at': datetime(2026, 11, 4, 7, tzinfo=UTC)},
])
def test_identity_evidence_after_ranking_generation_cannot_backdate_decision(kwargs):
    evidence = compose_egx_rank_binding(identity=identity(**kwargs), calendar=calendar())
    with pytest.raises(ValueError, match='ranking generation'):
        bind(evidence)


def test_calendar_evidence_after_ranking_generation_cannot_backdate_decision():
    late = calendar(observed_at=datetime(2026, 11, 4, 7, tzinfo=UTC))
    evidence = compose_egx_rank_binding(identity=identity(), calendar=late)
    with pytest.raises(ValueError, match='ranking generation'):
        bind(evidence)


def test_exact_retry_manifest_after_admission_observation_is_conservative():
    evidence = compose_egx_rank_binding(identity=identity(
        received_at=datetime(2026, 11, 4, 5, 10, tzinfo=UTC)), calendar=calendar())
    assert evidence.evidence_available_at == datetime(2026, 11, 4, 5, 10, tzinfo=UTC)
    assert bind(evidence)['candidate_count'] == 2


@pytest.mark.parametrize('kwargs', [
    {'session': date(2026, 11, 2)},
    {'available_at': datetime(2026, 11, 4, 6, tzinfo=UTC)},
    {'received_at': datetime(2026, 11, 4, 6, tzinfo=UTC)},
    {'available_at': OPEN, 'observed_at': OPEN},
])
def test_inconsistent_identity_evidence_fails_closed(kwargs):
    with pytest.raises(ValueError):
        compose_egx_rank_binding(identity=identity(**kwargs), calendar=calendar())


def test_wrong_types_rejected():
    with pytest.raises(ValueError):
        compose_egx_rank_binding(identity=identity().identity, calendar=calendar())
    with pytest.raises(ValueError):
        compose_egx_rank_binding(identity=identity(), calendar=calendar().model_dump())
    evidence = compose_egx_rank_binding(identity=identity(), calendar=calendar())
    with pytest.raises(ValueError):
        bind_retained_egx_rank_report(report(), expected_sha256=sha256(report()).hexdigest(),
                                      evidence=evidence.binding)


@pytest.mark.parametrize('field,value', [
    ('evidence_id', 'egx-calendar-sha256:' + 'd' * 64),
    ('artifact_sha256', 'D' * 64),
    ('observed_at', datetime(2026, 11, 3, 12, tzinfo=UTC)),
    ('admission', 'ADMITTED'),
])
def test_unchecked_calendar_copies_are_revalidated(field, value):
    forged = calendar().model_copy(update={field: value})
    with pytest.raises(ValueError):
        compose_egx_rank_binding(identity=identity(), calendar=forged)


@pytest.mark.parametrize('update', [
    lambda e: {'binding': e.binding.model_copy(update={'instrument_ids': (('CCCC', UUID(int=3)),)})},
    lambda e: {'binding': e.binding.model_copy(update={'fresh_until': NEXT_CLOSE})},
    lambda e: {'binding': e.binding.model_copy(update={'calendar_evidence_id': 'calendar-fixture'})},
    lambda e: {'evidence_available_at': datetime(2026, 11, 3, 13, tzinfo=UTC)},
    lambda e: {'calendar_admission': 'ADMITTED'},
    lambda e: {'live_money': True},
])
def test_unchecked_evidence_copies_are_revalidated_before_binding(update):
    evidence = compose_egx_rank_binding(identity=identity(), calendar=calendar())
    forged = evidence.model_copy(update=update(evidence))
    assert type(forged) is RetainedEgxRankBinding
    with pytest.raises(ValueError):
        bind(forged)


def test_calendar_projection_type_is_reused_not_duplicated():
    assert type(compose_egx_rank_binding(identity=identity(), calendar=calendar()).calendar) \
        is CalendarProjection
