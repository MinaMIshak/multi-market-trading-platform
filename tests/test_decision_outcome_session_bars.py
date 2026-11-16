"""decision-outcome-session-bars-v1: retained EGX daily rows + sessions -> outcome bars."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
from uuid import UUID

import pytest

from app.data.daily_canonical import CanonicalDailyBar, DailyBarSemanticClass
from app.decision_observation import canonical_bytes, decision_observation
from app.decision_outcome import TOUCH, OutcomeAssumptions, evaluate_decision_outcome
from app.decision_outcome_session_bars import (
    AVAILABILITY_RULE, CONTRACT_VERSION, VENUE_ID, bind_egx_outcome_session_bars)
from app.domain.models import MarketSession
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision

UTC = timezone.utc
AT = datetime(2026, 11, 5, 6, tzinfo=UTC)                 # Thursday 08:00 Cairo, pre-open
VALID_UNTIL = datetime(2026, 11, 9, 12, 30, tzinfo=UTC)   # Monday session close
RECEIVED = datetime(2026, 11, 9, 14, tzinfo=UTC)          # daily bytes received after Monday close
INSTRUMENT = UUID(int=7)
ZERO = OutcomeAssumptions(assumptions_version='fixture-zero', market='EGX',
                          entry_slippage_bps=D('0'), exit_slippage_bps=D('0'),
                          cost_bps_per_side=D('0'))


def observation(at=AT, market='EGX'):
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version='1',
                          config_id='fixture', symbol='TEST', decision_time=at,
                          data_cutoff=at - timedelta(minutes=1), state='READY',
                          entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    source = DecisionSource(market=market, symbol='TEST', instrument_id=INSTRUMENT,
                            source_id='fixture', artifact_sha256='a' * 64,
                            evidence_ids=('fixture-only',), data_as_of=candidate.data_cutoff,
                            available_at=at, fresh_until=at + timedelta(hours=1),
                            completed_bar=True)
    terms = DecisionTerms(entry_trigger=TOUCH, entry_low=D('9.5'), entry_high=D('10.5'),
                          entry_reference=D('10'), stop=D('9'), target=D('12'),
                          valid_until=VALID_UNTIL, max_holding_sessions=3, flat_by=None)
    record = decision_observation(TradeDecision(candidate=candidate, source=source, terms=terms),
                                  None, recorded_at=at + timedelta(minutes=5))
    return sha256(canonical_bytes(record)).hexdigest(), record


def session(day, status='VERIFIED'):
    d = date(2026, 11, day)
    if status in ('WEEKEND', 'HOLIDAY'):
        return MarketSession(market_date=d, status=status)
    opened = datetime(2026, 11, day, 8, tzinfo=UTC)       # 10:00 Cairo
    closed = datetime(2026, 11, day, 12, 30, tzinfo=UTC)  # 14:30 Cairo
    verified = closed + timedelta(minutes=30) if status == 'VERIFIED' else None
    return MarketSession(market_date=d, status=status, opened_at=opened, closed_at=closed,
                         data_verified_at=verified)


def row(day, o, h, l, c, n, *, quarantined=False, instrument=INSTRUMENT, digest='b' * 64):
    prices = dict(open=None, high=None, low=None, close=None, volume=None) if quarantined else dict(
        open=D(o), high=D(h), low=D(l), close=D(c), volume=D('1000'))
    return CanonicalDailyBar(
        instrument_id=instrument, canonical_symbol='TEST', provider_symbol='TEST.CA',
        market_date=date(2026, 11, day),
        semantic_class=(DailyBarSemanticClass.QUARANTINED_ANOMALY if quarantined
                        else DailyBarSemanticClass.VALID_EXECUTABLE),
        source_provider='fixture', source_snapshot_date=date(2026, 11, 9),
        source_row_number=n, source_sha256=digest, **prices)


CAL = (session(4), session(5), session(6, 'WEEKEND'), session(7, 'WEEKEND'), session(8), session(9))
LOOKBACK = (row(3, '9', '9.5', '8.8', '9.2', 1), row(4, '9.2', '9.9', '9.1', '9.8', 2))
ROWS = LOOKBACK + (row(5, '10', '10.6', '9.8', '10.4', 3),
                   row(8, '10.5', '12.3', '9.9', '12.1', 4),
                   row(9, '12', '12.2', '11.8', '12', 5))


def bind(rows=ROWS, sessions=CAL, received=RECEIVED, obs=None):
    identity, record = obs or observation()
    return bind_egx_outcome_session_bars(identity, record, tuple(rows), tuple(sessions),
                                         received_at=received)


def test_binds_verified_sessions_and_feeds_the_unchanged_evaluator():
    identity, record = observation()
    out = bind(obs=(identity, record))
    bars, complete, report = out['bars'], out['path_complete_through_at'], out['report']
    assert complete == RECEIVED
    # Look-back rows (Nov 3-4) are ignored; weekends carry no bar.
    assert [b.market_date.isoformat() for b in bars] == ['2026-11-05', '2026-11-08', '2026-11-09']
    assert report['sessions_bound'] == ['2026-11-05', '2026-11-08', '2026-11-09']
    assert report['nontrading_days'] == ['2026-11-06', '2026-11-07']
    assert report['pending_session'] is None
    first = bars[0]
    assert (first.instrument_id, first.symbol, first.venue_id) == (str(INSTRUMENT), 'TEST', VENUE_ID)
    assert first.session_id == 'XCAI:2026-11-05' and first.session_sequence == 1
    assert first.interval_start_utc == datetime(2026, 11, 5, 8, tzinfo=UTC)
    assert first.interval_end_utc == datetime(2026, 11, 5, 12, 30, tzinfo=UTC)
    assert (first.open, first.high, first.low, first.close) == (D('10'), D('10.6'), D('9.8'), D('10.4'))
    assert first.source_id == 'b' * 64 and first.provenance_id == 'fixture:2026-11-09:row-3'
    # AVAILABLE_AT_PATH_COMPLETION_V1: no bar is usable before the whole path is known.
    assert all(b.available_at_utc == complete for b in bars)

    outcome = evaluate_decision_outcome(identity, record, bars, assumptions=ZERO,
                                        path_complete_through_at=complete,
                                        evaluated_at=complete + timedelta(hours=1))
    assert (outcome['status'], outcome['exit']['reason']) == ('CLOSED', 'TARGET')
    assert outcome['exit']['market_date'] == '2026-11-08'
    assert D(outcome['metrics']['gross_r']) == 2 and outcome['metrics']['holding_sessions'] == 2
    assert [b['provenance_id'] for b in outcome['bars_used']] == [
        'fixture:2026-11-09:row-3', 'fixture:2026-11-09:row-4']


def test_report_is_labelled_and_claims_nothing():
    identity, record = observation()
    report = bind(obs=(identity, record))['report']
    assert report['contract_version'] == CONTRACT_VERSION == 'decision-outcome-session-bars-v1'
    assert report['observation_identity'] == identity
    assert report['decision_id'] == record['decision_id']
    assert report['daily_source'] == {
        'source_provider': 'fixture', 'source_snapshot_date': '2026-11-09',
        'source_sha256': 'b' * 64, 'received_at': '2026-11-09T14:00:00Z',
        'received_at_clock': 'CALLER_ASSERTED_UNVERIFIED'}
    assert report['calendar'] == {'first_date': '2026-11-04', 'last_date': '2026-11-09',
                                  'days': 6, 'admission': 'NOT_ESTABLISHED'}
    assert report['path_complete_through_at'] == '2026-11-09T14:00:00Z'
    assert report['availability_rule'] == AVAILABILITY_RULE
    assert report['corporate_actions'] == 'NOT_CHECKED'
    assert report['outcome'] == 'NOT_EVALUATED' and report['performance_claim'] == 'NONE'
    assert report['live_money'] is False and report['execution_allowed'] is False
    assert report['promotion'] == 'NOT_AUTHORIZED' and report['mode'] == 'SHADOW'


def test_session_not_closed_at_completion_is_pending_without_a_bar():
    received = datetime(2026, 11, 9, 7, tzinfo=UTC)    # Monday 09:00 Cairo, before the open
    out = bind(rows=ROWS[:-1], sessions=CAL[:-1] + (session(9, 'UNKNOWN'),), received=received)
    assert out['path_complete_through_at'] == received
    assert out['report']['sessions_bound'] == ['2026-11-05', '2026-11-08']
    assert out['report']['pending_session'] == '2026-11-09'
    with pytest.raises(ValueError, match='not closed at path completion'):
        bind(rows=ROWS, sessions=CAL[:-1] + (session(9, 'UNKNOWN'),), received=received)


def test_session_closed_after_receipt_fails_closed():
    # Monday is verified after close, but the daily bytes were received mid-session.
    with pytest.raises(ValueError, match='does not cover completed session'):
        bind(received=datetime(2026, 11, 9, 10, tzinfo=UTC))


@pytest.mark.parametrize('rows, match', [
    (ROWS[:3] + ROWS[4:], 'missing daily bar'),
    (ROWS[:3] + (row(8, '', '', '', '', 4, quarantined=True),) + ROWS[4:], 'quarantined'),
    (ROWS + (row(6, '10', '10', '10', '10', 6),), 'nontrading day'),
    (ROWS + (row(10, '10', '10', '10', '10', 6),), 'after calendar window'),
    (ROWS[:3] + (row(8, '10.5', '12.3', '9.9', '12.1', 4, digest='c' * 64),) + ROWS[4:],
     'one canonical artifact'),
    (tuple(row(d, '10', '10', '10', '10', d, instrument=UUID(int=8)) for d in (5, 8, 9)),
     'instrument or symbol mismatch'),
    (ROWS[:2] + (row(5, '10', '10', '10', '10', 3), row(5, '10', '10', '10', '10', 4)),
     'duplicate daily market_date'),
])
def test_incomplete_or_foreign_daily_rows_fail_closed(rows, match):
    with pytest.raises(ValueError, match=match):
        bind(rows=rows)


@pytest.mark.parametrize('sessions, match', [
    (CAL[:-1], 'path-completion date'),
    (CAL[2:], 'calendar starts after detection'),
    (CAL[:3] + CAL[4:], 'contiguous'),
    (CAL[:4] + (session(8, 'UNKNOWN'),) + CAL[5:], 'session state not established'),
    (CAL[:4] + (session(8, 'DATA_PENDING'),) + CAL[5:], 'session state not established'),
    (CAL[:4] + (session(8, 'WEEKEND'),) + CAL[5:], 'invalid EGX weekend'),
    (CAL[:2] + (MarketSession(market_date=date(2026, 11, 6), status='WEEKEND',
                              opened_at=datetime(2026, 11, 6, 8, tzinfo=UTC),
                              closed_at=datetime(2026, 11, 6, 12, tzinfo=UTC)),) + CAL[3:],
     'nontrading day contains trading hours'),
])
def test_unestablished_calendar_fails_closed(sessions, match):
    with pytest.raises(ValueError, match=match):
        bind(sessions=sessions)


def test_session_opened_before_detection_is_lookback_not_path():
    # Detected mid-session on Thursday: that session's bar would begin before detection.
    out = bind(obs=observation(at=datetime(2026, 11, 5, 9, tzinfo=UTC)))
    assert out['report']['sessions_bound'] == ['2026-11-08', '2026-11-09']


def test_clocks_and_market_fail_closed():
    with pytest.raises(ValueError, match='received before detection'):
        bind(received=AT - timedelta(hours=1))
    with pytest.raises(ValueError, match='timezone-aware received_at'):
        bind(received=RECEIVED.replace(tzinfo=None))
    with pytest.raises(ValueError, match='EGX observation required'):
        bind(obs=observation(market='US'))


def test_edited_record_and_unchecked_inputs_fail_closed():
    identity, record = observation()
    edited = deepcopy(record)
    edited['symbol'] = 'OTHER'
    with pytest.raises(ValueError):
        bind(obs=(identity, edited))
    forged = CanonicalDailyBar.model_construct(**{**ROWS[3].model_dump(), 'low': D('13')})
    with pytest.raises(ValueError):
        bind(rows=ROWS[:3] + (forged,) + ROWS[4:])
    with pytest.raises(ValueError, match='exact CanonicalDailyBar'):
        bind_egx_outcome_session_bars(identity, record, list(ROWS), CAL, received_at=RECEIVED)
    with pytest.raises(ValueError, match='exact MarketSession'):
        bind_egx_outcome_session_bars(identity, record, ROWS, list(CAL), received_at=RECEIVED)


def test_binding_is_deterministic_and_does_not_mutate_inputs():
    identity, record = observation()
    before = deepcopy(record)
    first, second = bind(obs=(identity, record)), bind(obs=(identity, record))
    assert first == second and record == before
