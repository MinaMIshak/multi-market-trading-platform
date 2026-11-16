"""decision-outcome-production-v1: retained observations + daily evidence -> retained outcomes."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
from uuid import UUID

import pytest

from app.decision_observation import canonical_bytes
from app.decision_observation_store import (
    load_retained_decision_observations, retain_decision_observation)
from app.decision_outcome import TOUCH, OutcomeAssumptions, evaluate_decision_outcome
from app.decision_outcome_metrics import aggregate_decision_outcomes
from app.decision_outcome_producer import (
    CALENDAR_RULE, CONTRACT_VERSION, ENTRY_STATUSES, PRODUCTION_REASONS,
    DecisionOutcomeProductionError, EgxDailyEvidence, produce_egx_decision_outcomes)
from app.decision_outcome_session_bars import bind_egx_outcome_session_bars
from app.decision_outcome_store import load_retained_decision_outcomes
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision
from tests.test_decision_outcome_session_bars import (
    AT, CAL, INSTRUMENT, LOOKBACK, RECEIVED, ROWS, VALID_UNTIL, ZERO, row, session)

UTC = timezone.utc
NOW = RECEIVED + timedelta(hours=1)
OTHER = UUID(int=8)
SAFETY = {'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
          'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
          'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The stores refuse group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


@pytest.fixture
def stores(tmp_path):
    observations, outcomes = tmp_path / 'observations', tmp_path / 'outcomes'
    observations.mkdir(mode=0o700)
    outcomes.mkdir(mode=0o700)
    return observations, outcomes


def decision(symbol='TEST', instrument=INSTRUMENT, at=AT):
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version='1',
                          config_id='fixture', symbol=symbol, decision_time=at,
                          data_cutoff=at - timedelta(minutes=1), state='READY',
                          entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    source = DecisionSource(market='EGX', symbol=symbol, instrument_id=instrument,
                            source_id='fixture', artifact_sha256='a' * 64,
                            evidence_ids=('fixture-only',), data_as_of=candidate.data_cutoff,
                            available_at=at, fresh_until=at + timedelta(hours=1),
                            completed_bar=True)
    terms = DecisionTerms(entry_trigger=TOUCH, entry_low=D('9.5'), entry_high=D('10.5'),
                          entry_reference=D('10'), stop=D('9'), target=D('12'),
                          valid_until=VALID_UNTIL, max_holding_sessions=3, flat_by=None)
    return TradeDecision(candidate=candidate, source=source, terms=terms)


def observe(directory, *decisions):
    for item in decisions or (decision(),):
        retain_decision_observation(str(directory), item, None, now=AT + timedelta(minutes=5))
    return load_retained_decision_observations(str(directory), market='EGX')


def produce(stores, daily=None, sessions=CAL, now=NOW, assumptions=ZERO):
    observations, outcomes = stores
    daily = {str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED)} if daily is None else daily
    return produce_egx_decision_outcomes(str(observations), str(outcomes), daily=daily,
                                         sessions=sessions, assumptions=assumptions, now=now)


def snapshot(directory):
    return sorted((p.name, p.read_bytes()) for p in directory.iterdir())


def refused(reason, call, *args, **kwargs):
    assert reason in PRODUCTION_REASONS
    with pytest.raises(DecisionOutcomeProductionError) as info:
        call(*args, **kwargs)
    assert info.value.reason == reason
    return info.value


def test_terminal_outcome_is_evaluated_from_retained_evidence_and_retained(stores):
    (identity, record), = observe(stores[0])
    out = produce(stores)
    assert out['schema_version'] == CONTRACT_VERSION == 'decision-outcome-production-v1'
    assert out['market'] == 'EGX' and out['observations'] == 1
    assert out['produced_at'] == '2026-11-09T15:00:00Z'
    (entry,) = out['outcomes']
    assert entry['observation_identity'] == identity
    assert entry['decision_id'] == record['decision_id']
    assert (entry['status'], entry['outcome_status'], entry['outcome_reason']) == (
        'RETAINED', 'CLOSED', None)
    assert entry['sessions_bound'] == ['2026-11-05', '2026-11-08', '2026-11-09']
    assert entry['path_complete_through_at'] == '2026-11-09T14:00:00Z'

    # The retained outcome is exactly the unchanged binding + evaluator result.
    bound = bind_egx_outcome_session_bars(identity, record, ROWS, CAL, received_at=RECEIVED)
    expected = evaluate_decision_outcome(identity, record, bound['bars'], assumptions=ZERO,
                                         path_complete_through_at=bound['path_complete_through_at'],
                                         evaluated_at=NOW)
    held = load_retained_decision_outcomes(str(stores[1]), market='EGX', assumptions=ZERO)
    assert held == (expected,)
    assert entry['outcome_identity'] == sha256(canonical_bytes(expected)).hexdigest()
    assert expected['exit']['reason'] == 'TARGET'

    metrics = aggregate_decision_outcomes(
        load_retained_decision_observations(str(stores[0]), market='EGX'), held,
        market='EGX', assumptions=ZERO, as_of=NOW)
    assert metrics['contract_version'] == 'decision-outcome-metrics-v1'


def test_result_is_labelled_and_claims_nothing(stores):
    observe(stores[0])
    out = produce(stores)
    assert {k: out[k] for k in SAFETY} == SAFETY
    assert out['calendar_rule'] == CALENDAR_RULE == 'CALENDAR_THROUGH_RECEIPT_DATE_V1'
    assert out['corporate_actions'] == 'NOT_CHECKED'
    assert out['calendar_admission'] == 'NOT_ESTABLISHED'
    assert out['daily_receipt_clock'] == 'CALLER_ASSERTED_UNVERIFIED'
    assert out['retained_outcome_reevaluation'] == 'NOT_PERFORMED'
    assert out['produced_at_clock'] == 'PRODUCER_CLOCK_UNVERIFIED'
    assert out['assumptions'] == ZERO.model_dump(mode='json')
    assert tuple(out['status_counts']) == ENTRY_STATUSES
    assert out['status_counts']['RETAINED'] == 1 and sum(out['status_counts'].values()) == 1
    assert out['retained_outcomes_without_observation'] == 0


def test_rerun_keeps_the_first_outcome_and_does_not_reevaluate(stores):
    observe(stores[0])
    first = produce(stores)
    before = snapshot(stores[1])
    later = produce(stores, now=NOW + timedelta(days=3))
    (entry,) = later['outcomes']
    assert entry['status'] == 'ALREADY_RETAINED'
    assert entry['outcome_identity'] == first['outcomes'][0]['outcome_identity']
    assert entry['outcome_status'] == 'CLOSED'
    assert snapshot(stores[1]) == before
    # A retained outcome is not re-evaluated even if its daily evidence is gone.
    assert produce(stores, daily={})['outcomes'][0]['status'] == 'ALREADY_RETAINED'


def test_open_position_is_pending_and_never_retained(stores):
    observe(stores[0])
    received = datetime(2026, 11, 6, 7, tzinfo=UTC)   # Friday (weekend) after Thursday's session
    rows = tuple(r.model_copy(update={'source_snapshot_date': date(2026, 11, 6)})
                 for r in LOOKBACK + (row(5, '10', '10.6', '9.8', '10.4', 3),))
    # The full calendar is offered; CALENDAR_THROUGH_RECEIPT_DATE_V1 trims it to Nov 5-6.
    out = produce(stores, daily={str(INSTRUMENT): EgxDailyEvidence(rows, received)},
                  now=received + timedelta(hours=1))
    (entry,) = out['outcomes']
    assert (entry['status'], entry['outcome_status'], entry['outcome_reason']) == (
        'PENDING_PATH', 'PENDING_PATH', 'POSITION_OPEN')
    assert entry['sessions_bound'] == ['2026-11-05'] and entry['outcome_identity'] is None
    assert snapshot(stores[1]) == []


def test_missing_or_early_daily_evidence_is_visible_not_evaluated(stores):
    observe(stores[0], decision(), decision(symbol='OTHR', instrument=OTHER))
    out = produce(stores, daily={str(OTHER): EgxDailyEvidence(ROWS, AT - timedelta(hours=1))})
    statuses = {e['symbol']: (e['status'], e['reason']) for e in out['outcomes']}
    assert statuses == {'TEST': ('AWAITING_DAILY_EVIDENCE', 'NO_DAILY_EVIDENCE'),
                        'OTHR': ('AWAITING_DAILY_EVIDENCE', 'RECEIVED_BEFORE_DETECTION')}
    assert out['status_counts']['AWAITING_DAILY_EVIDENCE'] == 2
    assert snapshot(stores[1]) == []
    # Without an outcome the unchanged aggregate counts each decision NOT_EVALUATED.
    metrics = aggregate_decision_outcomes(
        load_retained_decision_observations(str(stores[0]), market='EGX'), (),
        market='EGX', assumptions=ZERO, as_of=NOW)
    assert metrics['contract_version'] == 'decision-outcome-metrics-v1'


def test_gap_in_retained_evidence_rejects_only_that_observation(stores):
    observe(stores[0], decision(), decision(symbol='OTHR', instrument=OTHER))
    other_rows = tuple(r.model_copy(update={'instrument_id': OTHER, 'canonical_symbol': 'OTHR'})
                       for r in ROWS[:3] + ROWS[4:])       # Sunday's bar missing
    out = produce(stores, daily={str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED),
                                 str(OTHER): EgxDailyEvidence(other_rows, RECEIVED)})
    by_symbol = {e['symbol']: e for e in out['outcomes']}
    assert by_symbol['TEST']['status'] == 'RETAINED'
    rejected = by_symbol['OTHR']
    assert rejected['status'] == 'BINDING_REJECTED' and 'missing daily bar' in rejected['detail']
    assert rejected['outcome_identity'] is None and rejected['sessions_bound'] is None
    assert len(snapshot(stores[1])) == 1


def test_unestablished_session_rejects_binding(stores):
    observe(stores[0])
    sessions = CAL[:4] + (session(8, 'UNKNOWN'),) + CAL[5:]
    (entry,) = produce(stores, sessions=sessions)['outcomes']
    assert entry['status'] == 'BINDING_REJECTED'
    assert 'session state not established' in entry['detail']
    assert snapshot(stores[1]) == []


def test_observation_recorded_after_the_clock_is_not_evaluated(stores):
    observe(stores[0])
    (entry,) = produce(stores, daily={str(INSTRUMENT): EgxDailyEvidence(ROWS, AT)},
                       now=AT + timedelta(minutes=1))['outcomes']
    assert entry['status'] == 'OBSERVATION_AFTER_CLOCK'
    assert snapshot(stores[1]) == []


def test_invalid_configuration_is_refused(stores):
    observations, outcomes = stores
    evidence = {str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED)}
    us = OutcomeAssumptions(assumptions_version='fixture-zero', market='US',
                            entry_slippage_bps=D('0'), exit_slippage_bps=D('0'),
                            cost_bps_per_side=D('0'))
    call = produce_egx_decision_outcomes
    kwargs = dict(daily=evidence, sessions=CAL, assumptions=ZERO, now=NOW)
    refused('CONFIG_INVALID', call, 'relative', str(outcomes), **kwargs)
    refused('CONFIG_INVALID', call, str(observations), str(observations), **kwargs)
    refused('CONFIG_INVALID', call, str(observations), str(observations) + '/', **kwargs)
    for change in (dict(assumptions=us), dict(sessions=list(CAL)),
                   dict(daily={str(INSTRUMENT): (ROWS, RECEIVED)}),
                   dict(daily={str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED.replace(tzinfo=None))}),
                   dict(daily={str(INSTRUMENT): EgxDailyEvidence(ROWS, NOW + timedelta(seconds=1))})):
        refused('CONFIG_INVALID', call, str(observations), str(outcomes), **{**kwargs, **change})
    with pytest.raises(TypeError):
        call(str(observations), str(outcomes), **{**kwargs, 'now': NOW.replace(tzinfo=None)})
    assert snapshot(outcomes) == []


def test_damaged_stores_fail_closed(stores):
    observations, outcomes = stores
    observe(observations)
    (outcomes / 'unexpected.txt').write_bytes(b'x')
    error = refused('OUTCOMES_UNAVAILABLE', produce, stores)
    assert error.detail == 'STORE_INVALID'
    (outcomes / 'unexpected.txt').unlink()
    (observations / 'unexpected.txt').write_bytes(b'x')
    error = refused('OBSERVATIONS_UNAVAILABLE', produce, stores)
    assert error.detail == 'STORE_INVALID'
    assert snapshot(outcomes) == []


def test_production_is_deterministic_for_a_fixed_clock(tmp_path):
    results = []
    for name in ('a', 'b'):
        base = tmp_path / name
        base.mkdir(mode=0o700)
        pair = (base / 'observations', base / 'outcomes')
        for path in pair:
            path.mkdir(mode=0o700)
        observe(pair[0])
        results.append((produce(pair), snapshot(pair[1])))
    assert results[0] == results[1]
