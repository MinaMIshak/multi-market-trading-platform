"""Per-segment sample assessment under a declared policy; artificial records only."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
from uuid import UUID

import pytest

from app.decision_observation import canonical_bytes, decision_observation
from app.decision_outcome import OutcomeAssumptions, evaluate_decision_outcome
from app.decision_outcome_metrics import NOT_EVALUATED, aggregate_decision_outcomes
from app.decision_outcome_sample import (
    BELOW, CLOSED_BELOW_MINIMUM, CONTRACT_VERSION, MEETS, MISSING_ABOVE_MAXIMUM,
    SampleAssessmentPolicy, assess_decision_outcome_sample)
from app.paper.replay_models import PaperReplayBar
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision


AT = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
AS_OF = AT + timedelta(days=30)
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


def observation(symbol, number, offset, version='1', market='EGX'):
    at = day(offset)
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version=version,
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


def fixture():
    # Version 1: two closed outcomes (one win, one loss), nothing missing.
    # Version 2: one closed win and one decision without an outcome (NOT_EVALUATED).
    plan = (('A', 1, 0, '1', 'WIN'), ('B', 2, 2, '1', 'STP'),
            ('C', 3, 4, '2', 'WIN'), ('D', 4, 6, '2', None))
    observations, outcomes = {}, {}
    for symbol, number, offset, version, path in plan:
        observations[symbol] = observation(symbol, number, offset, version)
        if path is not None:
            outcomes[symbol] = outcome(observations[symbol], offset, path)
    return observations, outcomes


def policy(**overrides):
    values = dict(policy_version='fixture-sample-1', market='EGX', registered_at=day(-1),
                  min_closed_outcomes=2, max_missing_data_rate=D('0.25'))
    values.update(overrides)
    return SampleAssessmentPolicy(**values)


def assess(population, outcomes, market='EGX', assumptions=ZERO, as_of=AS_OF, rules=None):
    return assess_decision_outcome_sample(
        tuple(population), tuple(outcomes), market=market, assumptions=assumptions,
        as_of=as_of, policy=policy() if rules is None else rules)


def by_version(result):
    return {segment['strategy_version']: segment for segment in result['segments']}


def test_segments_are_assessed_separately_against_declared_minimums():
    observations, outcomes = fixture()
    result = assess(observations.values(), outcomes.values())
    assert result['contract_version'] == CONTRACT_VERSION == 'decision-outcome-sample-assessment-v1'
    assert result['metrics_contract_version'] == 'decision-outcome-metrics-v1'
    segments = by_version(result)
    assert segments['1'] == {
        'strategy_id': 'fixture-challenger', 'strategy_version': '1', 'config_id': 'fixture',
        'observations': 2, 'closed': 2, 'missing_data_rate': '0', 'state': MEETS,
        'reasons': [], 'registration': 'REGISTERED_AT_OR_BEFORE_FIRST_DETECTION'}
    assert segments['2']['state'] == BELOW
    assert segments['2']['reasons'] == [CLOSED_BELOW_MINIMUM, MISSING_ABOVE_MAXIMUM]
    assert (segments['2']['closed'], segments['2']['missing_data_rate']) == (1, '0.5')
    assert result['segment_state_counts'] == {MEETS: 1, BELOW: 1}
    # Metrics are the unchanged aggregate, embedded for inspection.
    whole = aggregate_decision_outcomes(tuple(observations.values()), tuple(outcomes.values()),
                                        market='EGX', assumptions=ZERO, as_of=AS_OF)
    assert result['metrics'] == whole
    assert result['population_sha256'] == whole['population_sha256']
    assert result['outcomes_sha256'] == whole['outcomes_sha256']
    assert (result['population_count'], result['outcome_count']) == (4, 3)
    # No cross-segment number at the top level.
    assert result['cross_segment_headline'] == 'NOT_COMPUTED'
    assert not {'hit_rate', 'expectancy_net_r', 'max_drawdown_r', 'state'} & set(result)


def test_omitting_a_loss_cannot_improve_the_assessment():
    observations, outcomes = fixture()
    without_loss = dict(outcomes)
    del without_loss['B']
    result = assess(observations.values(), without_loss.values())
    segment = by_version(result)['1']
    assert result['metrics']['segments'][0]['status_counts'][NOT_EVALUATED] == 1
    assert segment['state'] == BELOW
    assert segment['reasons'] == [CLOSED_BELOW_MINIMUM, MISSING_ABOVE_MAXIMUM]


def test_thresholds_are_inclusive_and_policy_driven():
    observations, outcomes = fixture()
    lenient = policy(min_closed_outcomes=1, max_missing_data_rate=D('0.5'))
    segments = by_version(assess(observations.values(), outcomes.values(), rules=lenient))
    assert segments['2']['state'] == MEETS and segments['2']['reasons'] == []
    strict = policy(min_closed_outcomes=3, max_missing_data_rate=D('0'))
    segments = by_version(assess(observations.values(), outcomes.values(), rules=strict))
    assert segments['1']['reasons'] == [CLOSED_BELOW_MINIMUM]
    assert segments['2']['reasons'] == [CLOSED_BELOW_MINIMUM, MISSING_ABOVE_MAXIMUM]


def test_registration_after_first_detection_is_visible():
    observations, outcomes = fixture()
    late = policy(registered_at=day(1))
    segments = by_version(assess(observations.values(), outcomes.values(), rules=late))
    assert segments['1']['registration'] == 'REGISTERED_AFTER_FIRST_DETECTION'
    assert segments['2']['registration'] == 'REGISTERED_AT_OR_BEFORE_FIRST_DETECTION'
    exact = policy(registered_at=day(0))
    segments = by_version(assess(observations.values(), outcomes.values(), rules=exact))
    assert segments['1']['registration'] == 'REGISTERED_AT_OR_BEFORE_FIRST_DETECTION'


@pytest.mark.parametrize('overrides', [
    {'min_closed_outcomes': 0},
    {'min_closed_outcomes': True},
    {'min_closed_outcomes': 2.0},
    {'min_closed_outcomes': 10001},
    {'max_missing_data_rate': D('1.5')},
    {'max_missing_data_rate': D('-0.1')},
    {'max_missing_data_rate': 0.25},
    {'max_missing_data_rate': '0.25'},
    {'max_missing_data_rate': D('NaN')},
    {'registered_at': day(-1).replace(tzinfo=None)},
    {'market': 'ALL'},
    {'policy_version': 'has space'},
    {'policy_version': ''},
    {'basis': 'VALIDATED'},
    {'registration_clock': 'VERIFIED'},
    {'promotion': 'AUTHORIZED'},
])
def test_policy_rules_fail_closed(overrides):
    with pytest.raises(ValueError):
        policy(**overrides)


def test_bypassed_or_foreign_policy_rejected():
    observations, outcomes = fixture()
    bad = SampleAssessmentPolicy.model_construct(
        schema_version='decision-outcome-sample-policy-v1', policy_version='x', market='EGX',
        registered_at=day(-1), registration_clock='CALLER_SUPPLIED_UNVERIFIED',
        min_closed_outcomes=0, max_missing_data_rate=D('0.25'), basis='DECLARED_UNVALIDATED')
    with pytest.raises(ValueError):
        assess(observations.values(), outcomes.values(), rules=bad)
    with pytest.raises(ValueError, match='SampleAssessmentPolicy'):
        assess(observations.values(), outcomes.values(),
               rules=policy().model_dump(mode='python'))
    with pytest.raises(ValueError, match='policy market'):
        assess(observations.values(), outcomes.values(), rules=policy(market='US'))
    with pytest.raises(ValueError, match='registered after as_of'):
        assess(observations.values(), outcomes.values(),
               rules=policy(registered_at=AS_OF + timedelta(seconds=1)))
    with pytest.raises(ValueError, match='timezone-aware'):
        assess(observations.values(), outcomes.values(), as_of=AS_OF.replace(tzinfo=None))
    with pytest.raises(ValueError, match='OutcomeAssumptions'):
        assess(observations.values(), outcomes.values(), assumptions=ZERO.model_dump())


def test_whole_input_still_fails_closed():
    observations, outcomes = fixture()
    stray = deepcopy(outcomes['A'])
    stray['observation_identity'] = 'e' * 64
    with pytest.raises(ValueError, match='population observation'):
        assess(observations.values(), list(outcomes.values()) + [stray])
    us = observation('USX', 9, 3, market='US')
    with pytest.raises(ValueError, match='another market'):
        assess(list(observations.values()) + [us], outcomes.values())
    edited = deepcopy(outcomes['B'])
    edited['live_money'] = True
    with pytest.raises(ValueError, match='live_money'):
        assess(observations.values(), dict(outcomes, B=edited).values())


def test_empty_population_has_no_segments():
    result = assess((), ())
    assert result['segments'] == [] and result['population_count'] == 0
    assert result['segment_state_counts'] == {MEETS: 0, BELOW: 0}


def test_deterministic_order_independent_and_labelled():
    observations, outcomes = fixture()
    first = assess(observations.values(), outcomes.values())
    again = assess(reversed(list(observations.values())), reversed(list(outcomes.values())))
    assert canonical_bytes(first) == canonical_bytes(again)
    assert first['policy_sha256'] == sha256(
        canonical_bytes(policy().model_dump(mode='json'))).hexdigest()
    assert first['policy']['max_missing_data_rate'] == '0.25'
    assert first['policy']['basis'] == 'DECLARED_UNVALIDATED'
    assert first['policy']['registration_clock'] == 'CALLER_SUPPLIED_UNVERIFIED'
    assert first['sample_assessment'] == 'ASSESSED_AGAINST_DECLARED_UNVALIDATED_POLICY'
    assert (first['statistical_significance'], first['empirical_validation']) \
        == ('NOT_ASSESSED', 'NOT_VALIDATED')
    assert (first['performance_claim'], first['execution_evidence']) == ('NONE', 'NONE')
    assert (first['mode'], first['live_money'], first['execution_allowed'], first['promotion']) \
        == ('SHADOW', False, False, 'NOT_AUTHORIZED')
    assert not {'signal', 'order', 'side', 'quantity'} & set(first)
