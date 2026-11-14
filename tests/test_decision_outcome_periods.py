"""Time-period segmentation of decision-outcome metrics; artificial records only."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
from uuid import UUID

import pytest

from app.decision_observation import canonical_bytes, decision_observation
from app.decision_outcome import OutcomeAssumptions, evaluate_decision_outcome
from app.decision_outcome_metrics import NOT_EVALUATED, aggregate_decision_outcomes
from app.decision_outcome_periods import (
    CONTRACT_VERSION, PERIOD_LABELS, EvaluationPeriod, EvaluationPeriodPolicy,
    aggregate_decision_outcomes_by_period)
from app.paper.replay_models import PaperReplayBar
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision


AT = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
AS_OF = AT + timedelta(days=60)
REGISTERED = AT + timedelta(days=24)
ZERO = OutcomeAssumptions(assumptions_version='fixture-zero', market='EGX',
                          entry_slippage_bps=D('0'), exit_slippage_bps=D('0'),
                          cost_bps_per_side=D('0'))
# Zero costs, R unit 1: WIN exits +2 on the next session, STP stops -1 on the fill session.
PATHS = {
    'WIN': [(0, '10', '10.6', '9.8', '10.4'), (1, '10.5', '12.3', '9.9', '12.1')],
    'STP': [(0, '10', '10.6', '8.9', '9.2')],
}


def day(n):
    return AT + timedelta(days=n)


def observation(symbol, number, offset, market='EGX'):
    at = day(offset)
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version='1',
                          config_id='fixture', symbol=symbol, decision_time=at,
                          data_cutoff=at - timedelta(minutes=1), state='READY',
                          entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    source = DecisionSource(market=market, symbol=symbol, instrument_id=UUID(int=number),
                            source_id='fixture', artifact_sha256='a' * 64,
                            evidence_ids=('fixture-only',), data_as_of=candidate.data_cutoff,
                            available_at=at, fresh_until=at + timedelta(hours=1),
                            completed_bar=True)
    terms = DecisionTerms(entry_trigger='LONG_ENTRY_ZONE_TOUCH_V1', entry_low=D('9.5'),
                          entry_high=D('10.5'), entry_reference=D('10'), stop=D('9'),
                          target=D('12'), valid_until=at + timedelta(days=3),
                          max_holding_sessions=3, flat_by=None)
    record = decision_observation(TradeDecision(candidate=candidate, source=source, terms=terms),
                                  None, recorded_at=at + timedelta(minutes=5))
    return sha256(canonical_bytes(record)).hexdigest(), record


def outcome(obs, offset, path):
    identity, record = obs
    bars = []
    for n, o, h, l, c in PATHS[path]:
        start = day(offset) + timedelta(hours=1, days=n)
        bars.append(PaperReplayBar(
            instrument_id=record['instrument_id'], venue_id='XCAI', symbol=record['symbol'],
            session_id=f'S{offset}-{n}', market_date=date(2026, 11, 2) + timedelta(days=offset + n),
            session_sequence=1, interval_start_utc=start,
            interval_end_utc=start + timedelta(hours=7),
            available_at_utc=start + timedelta(hours=7, minutes=30), open=D(o), high=D(h),
            low=D(l), close=D(c), volume=D('1000'), source_id='fixture-bars',
            provenance_id=f'prov-{offset}-{n}'))
    complete = day(offset + 10)
    return evaluate_decision_outcome(identity, record, tuple(bars), assumptions=ZERO,
                                     path_complete_through_at=complete,
                                     evaluated_at=complete + timedelta(hours=1))


def periods(*bounds):
    return tuple(EvaluationPeriod(label=label, start=day(start), end=day(end))
                 for label, start, end in bounds)


STANDARD = (('DEVELOPMENT', -1, 5), ('VALIDATION', 5, 15), ('OUT_OF_SAMPLE', 15, 25),
            ('FORWARD_SHADOW', 25, 40))


def policy(bounds=STANDARD, **overrides):
    values = dict(policy_version='fixture-periods-1', market='EGX', registered_at=REGISTERED,
                  periods=periods(*bounds))
    values.update(overrides)
    return EvaluationPeriodPolicy(**values)


def fixture():
    # Detection days: development 0, validation 10, out-of-sample 20 (no outcome),
    # forward 30, and 45 after every period (unassigned).
    plan = (('DEV', 1, 0, 'WIN'), ('VAL', 2, 10, 'STP'), ('OOS', 3, 20, None),
            ('FWD', 4, 30, 'WIN'), ('OUT', 5, 45, 'STP'))
    observations, outcomes = {}, {}
    for symbol, number, offset, path in plan:
        observations[symbol] = observation(symbol, number, offset)
        if path is not None:
            outcomes[symbol] = outcome(observations[symbol], offset, path)
    return observations, outcomes


def by_period(population, outcomes, market='EGX', assumptions=ZERO, as_of=AS_OF, rules=None):
    return aggregate_decision_outcomes_by_period(
        tuple(population), tuple(outcomes), market=market, assumptions=assumptions,
        as_of=as_of, policy=policy() if rules is None else rules)


def segment(period):
    [only] = period['metrics']['segments']
    return only


def test_periods_are_aggregated_separately_without_headline():
    observations, outcomes = fixture()
    result = by_period(observations.values(), outcomes.values())
    assert result['contract_version'] == CONTRACT_VERSION == 'decision-outcome-period-metrics-v1'
    assert result['metrics_contract_version'] == 'decision-outcome-metrics-v1'
    assert [p['label'] for p in result['periods']] == list(PERIOD_LABELS)
    assert [p['metrics']['population_count'] for p in result['periods']] == [1, 1, 1, 1]
    dev, val, oos, fwd = (segment(p) for p in result['periods'])
    assert (dev['wins'], dev['losses'], D(dev['total_net_r'])) == (1, 0, 2)
    assert (val['wins'], val['losses'], D(val['total_net_r'])) == (0, 1, -1)
    assert oos['status_counts'][NOT_EVALUATED] == 1 and oos['closed'] == 0
    assert oos['hit_rate'] is None and oos['missing_data_rate'] == '1'
    assert (fwd['wins'], fwd['observations']) == (1, 1)
    # The record detected after every period is disclosed, not dropped.
    assert result['unassigned']['label'] == 'UNASSIGNED'
    assert (result['unassigned']['observations'], result['unassigned']['outcomes']) == (1, 1)
    assert result['unassigned']['identities_sha256'] == sha256(
        canonical_bytes([observations['OUT'][0]])).hexdigest()
    assert (result['population_count'], result['outcome_count']) == (5, 4)
    whole = aggregate_decision_outcomes(tuple(observations.values()), tuple(outcomes.values()),
                                        market='EGX', assumptions=ZERO, as_of=AS_OF)
    assert result['population_sha256'] == whole['population_sha256']
    assert result['outcomes_sha256'] == whole['outcomes_sha256']
    # No cross-period number at the top level.
    assert result['cross_period_headline'] == 'NOT_COMPUTED'
    assert not {'segments', 'hit_rate', 'expectancy_net_r', 'max_drawdown_r'} & set(result)
    assert [p['registration'] for p in result['periods']] == [
        'REGISTERED_AFTER_START', 'REGISTERED_AFTER_START', 'REGISTERED_AFTER_START',
        'REGISTERED_AT_OR_BEFORE_START']
    assert all(p['end_reached_by_as_of'] for p in result['periods'])
    assert result['holdout_integrity'] == 'CALLER_ASSERTED_UNVERIFIED'
    assert result['policy']['registration_clock'] == 'CALLER_SUPPLIED_UNVERIFIED'
    assert result['assignment_basis'] == 'DETECTED_AT_HALF_OPEN_INTERVAL'


def test_assignment_uses_detection_time_half_open_not_exit_time():
    boundary = observation('EDGE', 6, 5)          # detected exactly at VALIDATION start
    late = observation('LATE', 7, 4)              # detected in DEVELOPMENT, exits on day 5
    late_outcome = outcome(late, 4, 'WIN')
    assert late_outcome['exit']['market_date'] == '2026-11-07'
    result = by_period([boundary, late], [late_outcome])
    dev, val = result['periods'][0]['metrics'], result['periods'][1]['metrics']
    assert dev['population_count'] == 1 and segment(result['periods'][0])['wins'] == 1
    assert val['population_count'] == 1
    assert segment(result['periods'][1])['status_counts'][NOT_EVALUATED] == 1


def test_gap_between_periods_is_unassigned_and_periods_may_be_empty():
    observations, outcomes = fixture()
    gapped = policy((('DEVELOPMENT', -1, 5), ('OUT_OF_SAMPLE', 15, 25)))
    result = by_period(observations.values(), outcomes.values(), rules=gapped)
    assert [p['label'] for p in result['periods']] == ['DEVELOPMENT', 'OUT_OF_SAMPLE']
    assert result['unassigned']['observations'] == 3      # days 10, 30 and 45
    assert result['unassigned']['outcomes'] == 3
    future = policy((('DEVELOPMENT', -1, 5), ('FORWARD_SHADOW', 70, 80)),
                    registered_at=AS_OF - timedelta(days=1))
    result = by_period(observations.values(), outcomes.values(), rules=future)
    forward = result['periods'][1]
    assert forward['metrics']['population_count'] == 0 and forward['metrics']['segments'] == []
    assert forward['end_reached_by_as_of'] is False


@pytest.mark.parametrize('bounds, overrides, match', [
    ((('VALIDATION', 0, 5), ('DEVELOPMENT', 5, 10)), {}, 'chronological'),
    ((('DEVELOPMENT', 0, 5), ('DEVELOPMENT', 5, 10)), {}, 'chronological'),
    ((('DEVELOPMENT', 0, 6), ('VALIDATION', 5, 10)), {}, 'overlap'),
    ((('DEVELOPMENT', 5, 5),), {}, 'precede'),
    ((('FORWARD_SHADOW', 20, 30),), {}, 'FORWARD_SHADOW'),
    ((), {}, 'at least 1'),
    (STANDARD, {'policy_version': 'has space'}, 'pattern'),
    (STANDARD, {'registered_at': REGISTERED.replace(tzinfo=None)}, 'timezone-aware'),
    (STANDARD, {'market': 'ALL'}, 'market'),
])
def test_policy_rules_fail_closed(bounds, overrides, match):
    with pytest.raises(ValueError, match=match):
        policy(bounds, **overrides)


def test_naive_and_unknown_period_values_rejected():
    with pytest.raises(ValueError, match='timezone-aware'):
        EvaluationPeriod(label='DEVELOPMENT', start=AT.replace(tzinfo=None), end=day(1))
    with pytest.raises(ValueError):
        EvaluationPeriod(label='TRAINING_ON_EVERYTHING', start=AT, end=day(1))
    with pytest.raises(ValueError):
        policy(registration_clock='VERIFIED')


def test_bypassed_or_foreign_policy_rejected():
    observations, outcomes = fixture()
    bad = EvaluationPeriodPolicy.model_construct(
        policy_version='x', market='EGX', registered_at=REGISTERED,
        periods=periods(('VALIDATION', 0, 5), ('DEVELOPMENT', 5, 10)),
        schema_version='decision-outcome-period-policy-v1',
        registration_clock='CALLER_SUPPLIED_UNVERIFIED',
        assignment_basis='DETECTED_AT_HALF_OPEN_INTERVAL')
    with pytest.raises(ValueError, match='chronological'):
        by_period(observations.values(), outcomes.values(), rules=bad)
    with pytest.raises(ValueError, match='EvaluationPeriodPolicy'):
        by_period(observations.values(), outcomes.values(),
                  rules=policy().model_dump(mode='python'))
    with pytest.raises(ValueError, match='policy market'):
        by_period(observations.values(), outcomes.values(), rules=policy(market='US'))
    with pytest.raises(ValueError, match='registered after as_of'):
        by_period(observations.values(), outcomes.values(),
                  rules=policy(STANDARD[:3], registered_at=AS_OF + timedelta(seconds=1)))
    with pytest.raises(ValueError, match='timezone-aware'):
        by_period(observations.values(), outcomes.values(), as_of=AS_OF.replace(tzinfo=None))


def test_whole_input_still_fails_closed_before_partitioning():
    observations, outcomes = fixture()
    stray = deepcopy(outcomes['DEV'])
    stray['observation_identity'] = 'e' * 64
    with pytest.raises(ValueError, match='population observation'):
        by_period(observations.values(), list(outcomes.values()) + [stray])
    with pytest.raises(ValueError, match='more than one outcome'):
        by_period(observations.values(), list(outcomes.values()) + [outcomes['VAL']])
    us = observation('USX', 9, 10, market='US')
    with pytest.raises(ValueError, match='another market'):
        by_period(list(observations.values()) + [us], outcomes.values())
    edited = deepcopy(outcomes['VAL'])
    edited['metrics']['net_r'] = '5'
    edited['live_money'] = True
    with pytest.raises(ValueError, match='live_money'):
        by_period(observations.values(), dict(outcomes, VAL=edited).values())


def test_deterministic_order_independent_and_labelled():
    observations, outcomes = fixture()
    first = by_period(observations.values(), outcomes.values())
    again = by_period(reversed(list(observations.values())), reversed(list(outcomes.values())))
    assert canonical_bytes(first) == canonical_bytes(again)
    assert first['policy_sha256'] == sha256(
        canonical_bytes(policy().model_dump(mode='json'))).hexdigest()
    assert first['policy']['policy_version'] == 'fixture-periods-1'
    assert (first['sample_assessment'], first['performance_claim'], first['execution_evidence']) \
        == ('NOT_ASSESSED', 'NONE', 'NONE')
    assert (first['mode'], first['live_money'], first['execution_allowed'], first['promotion']) \
        == ('SHADOW', False, False, 'NOT_AUTHORIZED')
    for period in first['periods']:
        assert period['metrics']['live_money'] is False
        assert period['metrics']['cross_segment_headline'] == 'NOT_COMPUTED'
    assert not {'signal', 'order', 'side', 'quantity'} & set(first)
