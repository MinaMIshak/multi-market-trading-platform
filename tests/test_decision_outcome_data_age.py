"""Data-age (freshness at detection) segmentation of decision-outcome metrics.

Artificial records only; nothing here is a real market observation.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
from uuid import UUID

import pytest

from app.decision_observation import canonical_bytes, decision_observation
from app.decision_outcome import OutcomeAssumptions, evaluate_decision_outcome
from app.decision_outcome_data_age import (
    AGE_BASIS, CONTRACT_VERSION, DataAgePolicy, aggregate_decision_outcomes_by_data_age,
    bucket_labels)
from app.decision_outcome_metrics import NOT_EVALUATED, aggregate_decision_outcomes
from app.paper.replay_models import PaperReplayBar
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision


AT = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
AS_OF = AT + timedelta(days=60)
ZERO = OutcomeAssumptions(assumptions_version='fixture-zero', market='EGX',
                          entry_slippage_bps=D('0'), exit_slippage_bps=D('0'),
                          cost_bps_per_side=D('0'))
# Zero costs, R unit 1: WIN exits +2 on the next session, STP stops -1 on the fill session.
PATHS = {
    'WIN': [(0, '10', '10.6', '9.8', '10.4'), (1, '10.5', '12.3', '9.9', '12.1')],
    'STP': [(0, '10', '10.6', '8.9', '9.2')],
}
BOUNDS = (3600, 86400)
LABELS = ('AGE_LE_3600S', 'AGE_GT_3600S_LE_86400S', 'AGE_GT_86400S')


def day(n):
    return AT + timedelta(days=n)


def policy(bounds=BOUNDS, market='EGX', registered_at=AT - timedelta(days=1)):
    return DataAgePolicy(policy_version='fixture-age-1', market=market,
                         registered_at=registered_at, upper_bounds_seconds=bounds)


def observation(symbol, number, offset, age, market='EGX'):
    at = day(offset)
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version='1',
                          config_id='fixture', symbol=symbol, decision_time=at,
                          data_cutoff=at - age, state='READY',
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
    # Exactly 3600 s and exactly 86400 s sit on inclusive upper edges.
    plan = (('NEW', 1, 0, timedelta(seconds=60), 'WIN'),
            ('EDGE', 2, 2, timedelta(seconds=3600), 'STP'),
            ('DAY', 3, 4, timedelta(seconds=86400), 'WIN'),
            ('OLD', 4, 6, timedelta(days=3), 'STP'),
            ('OLDNONE', 5, 8, timedelta(days=3, seconds=1), None))
    observations, outcomes = {}, {}
    for symbol, number, offset, age, path in plan:
        observations[symbol] = observation(symbol, number, offset, age)
        if path is not None:
            outcomes[symbol] = outcome(observations[symbol], offset, path)
    return observations, outcomes


def by_age(population, outcomes, market='EGX', assumptions=ZERO, as_of=AS_OF, age_policy=None):
    return aggregate_decision_outcomes_by_data_age(
        tuple(population), tuple(outcomes), market=market, assumptions=assumptions,
        as_of=as_of, policy=policy() if age_policy is None else age_policy)


def buckets(result):
    return {entry['label']: entry for entry in result['buckets']}


def segment(entry):
    [only] = entry['metrics']['segments']
    return only


def readdressed(obs, **changes):
    _, record = obs
    record = deepcopy(record)
    record.update(changes)
    return sha256(canonical_bytes(record)).hexdigest(), record


def test_buckets_are_aggregated_separately_without_headline():
    observations, outcomes = fixture()
    result = by_age(observations.values(), outcomes.values())
    assert result['contract_version'] == CONTRACT_VERSION == 'decision-outcome-data-age-metrics-v1'
    assert result['metrics_contract_version'] == 'decision-outcome-metrics-v1'
    assert result['bucket_labels'] == list(LABELS) == [e['label'] for e in result['buckets']]
    assert result['bucket_counts'] == {LABELS[0]: 2, LABELS[1]: 1, LABELS[2]: 2}
    by = buckets(result)
    assert (by[LABELS[0]]['lower_exclusive_seconds'], by[LABELS[0]]['upper_inclusive_seconds']) \
        == (None, 3600)
    assert (by[LABELS[1]]['lower_exclusive_seconds'], by[LABELS[1]]['upper_inclusive_seconds']) \
        == (3600, 86400)
    assert (by[LABELS[2]]['lower_exclusive_seconds'], by[LABELS[2]]['upper_inclusive_seconds']) \
        == (86400, None)
    fresh = segment(by[LABELS[0]])
    assert (fresh['observations'], fresh['wins'], fresh['losses'], D(fresh['total_net_r'])) \
        == (2, 1, 1, 1)
    middle = segment(by[LABELS[1]])
    assert (middle['wins'], D(middle['total_net_r'])) == (1, 2)
    # A record without an outcome is NOT_EVALUATED in its own bucket, not dropped.
    old = segment(by[LABELS[2]])
    assert (old['observations'], old['losses'], old['status_counts'][NOT_EVALUATED]) == (2, 1, 1)
    assert sum(entry['observations'] for entry in result['buckets']) == 5
    assert sum(entry['metrics']['outcome_count'] for entry in result['buckets']) == 4
    whole = aggregate_decision_outcomes(tuple(observations.values()), tuple(outcomes.values()),
                                        market='EGX', assumptions=ZERO, as_of=AS_OF)
    assert (result['population_count'], result['outcome_count']) == (5, 4)
    assert result['population_sha256'] == whole['population_sha256']
    assert result['outcomes_sha256'] == whole['outcomes_sha256']
    assert result['cross_bucket_headline'] == 'NOT_COMPUTED'
    assert not {'segments', 'hit_rate', 'expectancy_net_r', 'max_drawdown_r'} & set(result)


def test_empty_buckets_are_still_reported():
    observations, outcomes = fixture()
    result = by_age(observations.values(), outcomes.values(),
                    age_policy=policy(bounds=(10, 30, 3600, 86400, 7 * 86400)))
    assert result['bucket_labels'] == ['AGE_LE_10S', 'AGE_GT_10S_LE_30S', 'AGE_GT_30S_LE_3600S',
                                       'AGE_GT_3600S_LE_86400S', 'AGE_GT_86400S_LE_604800S',
                                       'AGE_GT_604800S']
    assert [e['observations'] for e in result['buckets']] == [0, 0, 2, 1, 2, 0]
    for entry in (result['buckets'][0], result['buckets'][1], result['buckets'][5]):
        assert entry['metrics']['population_count'] == 0 and entry['metrics']['segments'] == []
    empty = by_age((), ())
    assert empty['population_count'] == 0 and empty['policy_registration'] == 'NO_OBSERVATIONS'
    assert [e['observations'] for e in empty['buckets']] == [0, 0, 0]
    assert bucket_labels((5,)) == ('AGE_LE_5S', 'AGE_GT_5S')


def test_labels_policy_and_safety_without_interpretation():
    observations, outcomes = fixture()
    result = by_age(observations.values(), outcomes.values())
    assert result['age_basis'] == AGE_BASIS
    assert 'NOT_SESSION_AWARE' in AGE_BASIS
    assert result['source_freshness_at_detection'] == 'WITHIN_SOURCE_DECLARED_FRESH_UNTIL'
    assert result['policy']['basis'] == 'DECLARED_UNVALIDATED'
    assert result['policy']['registration_clock'] == 'CALLER_SUPPLIED_UNVERIFIED'
    assert result['policy']['upper_bounds_seconds'] == [3600, 86400]
    assert result['policy_sha256'] == sha256(canonical_bytes(result['policy'])).hexdigest()
    assert result['policy_registration'] == 'REGISTERED_AT_OR_BEFORE_FIRST_DETECTION'
    late = by_age(observations.values(), outcomes.values(), age_policy=policy(registered_at=AS_OF))
    assert late['policy_registration'] == 'REGISTERED_AFTER_FIRST_DETECTION'
    assert (result['age_interpretation'], result['trading_effect']) == ('NOT_EVALUATED', 'NONE')
    assert (result['sample_assessment'], result['performance_claim'], result['execution_evidence']) \
        == ('NOT_ASSESSED', 'NONE', 'NONE')
    assert (result['mode'], result['live_money'], result['execution_allowed'], result['promotion']) \
        == ('SHADOW', False, False, 'NOT_AUTHORIZED')
    for entry in result['buckets']:
        assert entry['metrics']['live_money'] is False
        assert entry['metrics']['cross_segment_headline'] == 'NOT_COMPUTED'
    assert not {'signal', 'order', 'side', 'quantity', 'risk', 'score'} & set(result)


def test_assignment_uses_retained_data_age_not_the_outcome():
    # Same path, different retained data age: each lands in its own bucket only.
    young = observation('A', 11, 0, timedelta(seconds=30))
    aged = observation('B', 12, 0, timedelta(days=2))
    result = by_age([young, aged], [outcome(young, 0, 'WIN'), outcome(aged, 0, 'WIN')])
    by = buckets(result)
    assert segment(by[LABELS[0]])['wins'] == segment(by[LABELS[2]])['wins'] == 1
    assert by[LABELS[1]]['observations'] == 0


@pytest.mark.parametrize('changes', [
    {'data_as_of': (AT + timedelta(days=4, seconds=1)).isoformat()},
    {'available_at': (AT + timedelta(days=4, seconds=1)).isoformat()},
    {'data_as_of': (AT + timedelta(days=4, minutes=1)).isoformat(),
     'available_at': (AT + timedelta(days=4, seconds=30)).isoformat()},
])
def test_inconsistent_source_times_fail_closed(changes):
    observations, outcomes = fixture()
    bad = readdressed(observations['DAY'], **changes)
    rest = {key: value for key, value in outcomes.items() if key != 'DAY'}
    with pytest.raises(ValueError, match='source times'):
        by_age(dict(observations, DAY=bad).values(), rest.values())


@pytest.mark.parametrize('bounds', [(), (0,), (-5,), (3600, 3600), (86400, 3600), (True,),
                                    (366 * 86400 + 1,), tuple(range(1, 10))])
def test_invalid_policy_bounds_fail_closed(bounds):
    with pytest.raises(ValueError):
        policy(bounds=bounds)


def test_policy_is_revalidated_and_bound_to_market_and_time():
    observations, outcomes = fixture()
    bypass = DataAgePolicy.model_construct(policy_version='x', market='EGX',
                                           registered_at=AT, upper_bounds_seconds=(10, 5))
    with pytest.raises(ValueError, match='ascending'):
        by_age(observations.values(), outcomes.values(), age_policy=bypass)
    with pytest.raises(ValueError, match='DataAgePolicy'):
        by_age(observations.values(), outcomes.values(),
               age_policy=policy().model_dump(mode='python'))
    with pytest.raises(ValueError, match='market mismatch'):
        by_age(observations.values(), outcomes.values(), age_policy=policy(market='US'))
    with pytest.raises(ValueError, match='registered after as_of'):
        by_age(observations.values(), outcomes.values(),
               age_policy=policy(registered_at=AS_OF + timedelta(seconds=1)))
    with pytest.raises(ValueError, match='timezone-aware'):
        policy(registered_at=AT.replace(tzinfo=None))


def test_whole_input_still_fails_closed_before_partitioning():
    observations, outcomes = fixture()
    stray = deepcopy(outcomes['NEW'])
    stray['observation_identity'] = 'e' * 64
    with pytest.raises(ValueError, match='population observation'):
        by_age(observations.values(), list(outcomes.values()) + [stray])
    with pytest.raises(ValueError, match='more than one outcome'):
        by_age(observations.values(), list(outcomes.values()) + [outcomes['OLD']])
    us = observation('USX', 9, 10, timedelta(seconds=60), market='US')
    with pytest.raises(ValueError, match='another market'):
        by_age(list(observations.values()) + [us], outcomes.values())
    # A data_as_of edited without re-addressing is caught by the content address.
    identity, record = observations['OLD']
    tampered = deepcopy(record)
    tampered['data_as_of'] = record['detected_at']
    with pytest.raises(ValueError):
        by_age(dict(observations, OLD=(identity, tampered)).values(), outcomes.values())
    with pytest.raises(ValueError, match='OutcomeAssumptions'):
        by_age(observations.values(), outcomes.values(),
               assumptions=ZERO.model_dump(mode='python'))
    with pytest.raises(ValueError, match='timezone-aware'):
        by_age(observations.values(), outcomes.values(), as_of=AS_OF.replace(tzinfo=None))


def test_deterministic_and_order_independent():
    observations, outcomes = fixture()
    first = by_age(observations.values(), outcomes.values())
    again = by_age(reversed(list(observations.values())), reversed(list(outcomes.values())))
    assert canonical_bytes(first) == canonical_bytes(again)
