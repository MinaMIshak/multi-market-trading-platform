from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal as D, localcontext
from hashlib import sha256
from uuid import UUID

import pytest

from app.decision_observation import canonical_bytes, decision_observation
from app.decision_outcome import OutcomeAssumptions, evaluate_decision_outcome
from app.decision_outcome_metrics import (
    CONTRACT_VERSION, DEFINITIONS_VERSION, NOT_EVALUATED, aggregate_decision_outcomes)
from app.paper.replay_models import PaperReplayBar
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision


AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
VALID_UNTIL = AT + timedelta(days=3)
COMPLETE = datetime(2026, 11, 9, tzinfo=timezone.utc)
EVALUATED = COMPLETE + timedelta(hours=1)
AS_OF = COMPLETE + timedelta(hours=2)
ZERO = OutcomeAssumptions(assumptions_version='fixture-zero', market='EGX',
                          entry_slippage_bps=D('0'), exit_slippage_bps=D('0'),
                          cost_bps_per_side=D('0'))


def exact(numerator, denominator):
    with localcontext(Context(prec=34)):
        return str(D(numerator) / D(denominator))


def observation(symbol, number, version='1', market='EGX'):
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version=version,
                          config_id='fixture', symbol=symbol, decision_time=AT,
                          data_cutoff=AT - timedelta(minutes=1), state='READY',
                          entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    source = DecisionSource(market=market, symbol=symbol, instrument_id=UUID(int=number),
                            source_id='fixture', artifact_sha256='a' * 64,
                            evidence_ids=('fixture-only',), data_as_of=candidate.data_cutoff,
                            available_at=AT, fresh_until=AT + timedelta(hours=1),
                            completed_bar=True)
    terms = DecisionTerms(entry_trigger='LONG_ENTRY_ZONE_TOUCH_V1', entry_low=D('9.5'),
                          entry_high=D('10.5'), entry_reference=D('10'), stop=D('9'),
                          target=D('12'), valid_until=VALID_UNTIL, max_holding_sessions=3,
                          flat_by=None)
    record = decision_observation(TradeDecision(candidate=candidate, source=source, terms=terms),
                                  None, recorded_at=AT + timedelta(minutes=5))
    return sha256(canonical_bytes(record)).hexdigest(), record


def bar(record, day, o, h, l, c):
    start = datetime(2026, 11, 5, 13, tzinfo=timezone.utc) + timedelta(days=day)
    return PaperReplayBar(
        instrument_id=record['instrument_id'], venue_id='XCAI', symbol=record['symbol'],
        session_id=f'S{day}', market_date=date(2026, 11, 5) + timedelta(days=day),
        session_sequence=1, interval_start_utc=start, interval_end_utc=start + timedelta(hours=7),
        available_at_utc=start + timedelta(hours=7, minutes=30), open=D(o), high=D(h), low=D(l),
        close=D(c), volume=D('1000'), source_id='fixture-bars', provenance_id=f'prov-{day}')


# Bar paths (zero costs, R unit 1): win +2 exits day 1, gap stop -1.5 exits day 2,
# fill-session stop -1 exits day 0, never touched, no bars in the entry window.
PATHS = {
    'WIN': [(0, '10', '10.6', '9.8', '10.4'), (1, '10.5', '12.3', '9.9', '12.1')],
    'GAP': [(0, '10', '10.6', '9.8', '10.4'), (1, '10.4', '10.9', '9.6', '10.6'),
            (2, '8.5', '8.8', '8.2', '8.6')],
    'STP': [(0, '10', '10.6', '8.9', '9.2')],
    'NOT': [(day, '10.8', '11', '10.6', '10.9') for day in range(3)],
    'NOB': [],
}


def outcome(obs, path, assumptions=ZERO):
    identity, record = obs
    bars = tuple(bar(record, *values) for values in PATHS[path])
    return evaluate_decision_outcome(identity, record, bars, assumptions=assumptions,
                                     path_complete_through_at=COMPLETE, evaluated_at=EVALUATED)


def fixture(version='1'):
    observations = {name: observation(name, number, version)
                    for number, name in enumerate(('WIN', 'GAP', 'STP', 'NOT', 'NOB', 'MIS'), 1)}
    outcomes = {name: outcome(observations[name], name) for name in PATHS}
    return observations, outcomes


def aggregate(population, outcomes, market='EGX', assumptions=ZERO, as_of=AS_OF):
    return aggregate_decision_outcomes(tuple(population), tuple(outcomes), market=market,
                                       assumptions=assumptions, as_of=as_of)


def test_segment_metrics_expose_sample_size_and_not_evaluated():
    observations, outcomes = fixture()
    result = aggregate(observations.values(), outcomes.values())
    assert result['contract_version'] == CONTRACT_VERSION == 'decision-outcome-metrics-v1'
    assert result['definitions_version'] == DEFINITIONS_VERSION
    assert (result['population_count'], result['outcome_count'], result['not_evaluated_count']) \
        == (6, 5, 1)
    [segment] = result['segments']
    assert (segment['strategy_id'], segment['strategy_version'], segment['config_id']) == \
        ('fixture-challenger', '1', 'fixture')
    assert segment['observations'] == 6
    assert segment['status_counts'] == {'CLOSED': 3, 'NO_ENTRY': 1, 'PENDING_PATH': 0,
                                        'UNEVALUABLE': 1, NOT_EVALUATED: 1}
    assert segment['reason_counts'] == {'NO_ENTRY:NOT_TOUCHED_BEFORE_EXPIRY': 1,
                                        'UNEVALUABLE:NO_SESSION_BARS_IN_ENTRY_WINDOW': 1}
    assert segment['exit_reason_counts'] == {'STOP': 2, 'TARGET': 1, 'TIME_EXIT': 0}
    assert (segment['closed'], segment['wins'], segment['losses'], segment['flat']) == (3, 1, 2, 0)
    assert segment['hit_rate'] == exact(1, 3)
    assert D(segment['mean_win_net_r']) == 2 and D(segment['mean_loss_net_r']) == D('-1.25')
    assert D(segment['payoff_ratio']) == D('1.6')
    assert segment['expectancy_net_r'] == exact('-0.5', 3)
    assert segment['expectancy_gross_r'] == segment['expectancy_net_r']
    assert D(segment['total_net_r']) == D('-0.5')
    # Exit order day 0 (-1), day 1 (+2), day 2 (-1.5): peak 1, trough -0.5.
    assert D(segment['max_drawdown_r']) == D('1.5')
    assert segment['mean_mae_r'] == exact(D('-0.2') - D('1.5') - 1, 3)
    assert segment['missing_data_rate'] == exact(2, 6)
    assert segment['max_holding_sessions'] == 3
    assert segment['mean_holding_sessions'] == exact(2 + 3 + 1, 3)
    assert segment['detected_from'] == segment['detected_through'] == '2026-11-05T12:00:00Z'


def test_omitted_loss_outcome_is_counted_not_dropped():
    observations, outcomes = fixture()
    kept = [value for name, value in outcomes.items() if name != 'GAP']
    result = aggregate(observations.values(), kept)
    [segment] = result['segments']
    assert result['not_evaluated_count'] == 2 and segment['observations'] == 6
    assert segment['status_counts'][NOT_EVALUATED] == 2 and segment['losses'] == 1
    assert segment['missing_data_rate'] == exact(3, 6)


def test_strategy_versions_are_separate_segments_without_headline():
    first, first_outcomes = fixture('1')
    second = {name: observation(name + '2', number, '2')
              for number, name in enumerate(('WIN', 'STP'), 20)}
    second_outcomes = [outcome(second[name], name) for name in second]
    result = aggregate(list(first.values()) + list(second.values()),
                       list(first_outcomes.values()) + second_outcomes)
    assert [s['strategy_version'] for s in result['segments']] == ['1', '2']
    assert [s['observations'] for s in result['segments']] == [6, 2]
    assert result['segments'][1]['hit_rate'] == exact(1, 2)
    assert result['cross_segment_headline'] == 'NOT_COMPUTED'
    assert not {'hit_rate', 'expectancy_net_r', 'max_drawdown_r'} & set(result)


def test_markets_and_assumptions_stay_separate():
    observations, outcomes = fixture()
    us = observation('USX', 40, market='US')
    with pytest.raises(ValueError, match='another market'):
        aggregate(list(observations.values()) + [us], outcomes.values())
    with pytest.raises(ValueError, match='market'):
        aggregate(observations.values(), outcomes.values(), market='US')
    other = OutcomeAssumptions(assumptions_version='fixture-costs', market='EGX',
                               entry_slippage_bps=D('10'), exit_slippage_bps=D('10'),
                               cost_bps_per_side=D('5'))
    with pytest.raises(ValueError, match='assumptions'):
        aggregate(observations.values(), outcomes.values(), assumptions=other)
    mixed = dict(outcomes, WIN=outcome(observations['WIN'], 'WIN', assumptions=other))
    with pytest.raises(ValueError, match='assumptions'):
        aggregate(observations.values(), mixed.values())
    with pytest.raises(ValueError, match='OutcomeAssumptions'):
        aggregate(observations.values(), outcomes.values(), assumptions={'market': 'EGX'})


def _edited(outcomes, name, change):
    edited = deepcopy(outcomes[name])
    change(edited)
    return list(dict(outcomes, **{name: edited}).values())


@pytest.mark.parametrize('name, change, match', [
    ('WIN', lambda o: o.update(strategy_version='2'), 'strategy_version'),
    ('WIN', lambda o: o.update(decision_id='f' * 64), 'decision_id'),
    ('WIN', lambda o: o.update(live_money=True), 'live_money'),
    ('WIN', lambda o: o.update(performance_claim='EDGE'), 'performance_claim'),
    ('WIN', lambda o: o.update(rules_version='other'), 'rules_version'),
    ('WIN', lambda o: o.update(metrics=None), 'CLOSED'),
    ('WIN', lambda o: o['metrics'].update(net_r='NaN'), 'finite'),
    ('WIN', lambda o: o['metrics'].update(mae_r='0.5'), 'excursion'),
    ('NOT', lambda o: o.update(reason='POSITION_OPEN'), 'unregistered'),
    ('NOT', lambda o: o.update(metrics={}), 'only CLOSED'),
    ('WIN', lambda o: o.update(observation_identity='e' * 64), 'population observation'),
    ('WIN', lambda o: o.update(extra=1), 'field set'),
])
def test_outcome_structure_and_binding_fail_closed(name, change, match):
    observations, outcomes = fixture()
    with pytest.raises(ValueError, match=match):
        aggregate(observations.values(), _edited(outcomes, name, change))


def test_duplicate_outcomes_and_population_entries_fail_closed():
    observations, outcomes = fixture()
    with pytest.raises(ValueError, match='more than one outcome'):
        aggregate(observations.values(), list(outcomes.values()) + [outcomes['WIN']])
    with pytest.raises(ValueError, match='duplicate'):
        aggregate(list(observations.values()) + [observations['WIN']], outcomes.values())


def test_population_is_bound_to_content_address():
    observations, outcomes = fixture()
    identity, record = observations['WIN']
    edited = deepcopy(record)
    edited['terms']['target'] = '13'
    with pytest.raises(ValueError):
        aggregate(dict(observations, WIN=(identity, edited)).values(), outcomes.values())
    with pytest.raises(ValueError):
        aggregate(dict(observations, WIN=('b' * 64, record)).values(), [])
    with pytest.raises(ValueError):
        aggregate_decision_outcomes(list(observations.values()), (), market='EGX',
                                    assumptions=ZERO, as_of=AS_OF)


def test_as_of_bounds_records_and_outcomes():
    observations, outcomes = fixture()
    with pytest.raises(ValueError, match='as_of'):
        aggregate(observations.values(), outcomes.values(), as_of=EVALUATED - timedelta(seconds=1))
    early = AT + timedelta(minutes=1)          # before every record was recorded
    with pytest.raises(ValueError, match='recorded after as_of'):
        aggregate(observations.values(), [], as_of=early)
    with pytest.raises(ValueError, match='timezone-aware'):
        aggregate(observations.values(), outcomes.values(), as_of=AS_OF.replace(tzinfo=None))


def test_deterministic_order_independent_and_labelled():
    observations, outcomes = fixture()
    first = aggregate(observations.values(), outcomes.values())
    reordered = aggregate(reversed(list(observations.values())),
                          reversed(list(outcomes.values())))
    assert canonical_bytes(first) == canonical_bytes(reordered)
    assert first['population_completeness'] == 'CALLER_ASSERTED_UNVERIFIED'
    assert first['outcome_integrity'] == 'STRUCTURE_AND_OBSERVATION_BINDING_CHECKED_NOT_RECOMPUTED'
    assert first['sample_assessment'] == 'NOT_ASSESSED'
    assert first['assumptions']['assumptions_version'] == 'fixture-zero'
    assert first['definitions']['false_positive_rate'] == 'NOT_DEFINED'
    assert (first['evidence_kind'], first['execution_evidence'], first['performance_claim']) == \
        ('HYPOTHETICAL_FROM_SESSION_BARS', 'NONE', 'NONE')
    assert (first['mode'], first['live_money'], first['execution_allowed'], first['promotion']) \
        == ('SHADOW', False, False, 'NOT_AUTHORIZED')
    assert not {'signal', 'order', 'side', 'quantity'} & set(first)


def test_empty_population_reports_zero_sample_without_ratios():
    result = aggregate((), ())
    assert (result['population_count'], result['outcome_count'], result['segments']) == (0, 0, [])
    observations, _ = fixture()
    [segment] = aggregate([observations['NOB']], [])['segments']
    assert segment['status_counts'][NOT_EVALUATED] == 1 and segment['closed'] == 0
    assert segment['hit_rate'] is None and segment['expectancy_net_r'] is None
    assert segment['payoff_ratio'] is None and segment['max_holding_sessions'] is None
    assert segment['max_drawdown_r'] == '0' and segment['missing_data_rate'] == '1'
