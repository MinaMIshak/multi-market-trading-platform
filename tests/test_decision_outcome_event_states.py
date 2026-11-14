"""Event-risk-state segmentation of decision-outcome metrics; artificial records only."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
from uuid import UUID

import pytest

from app.decision_observation import canonical_bytes, decision_observation
from app.decision_outcome import OutcomeAssumptions, evaluate_decision_outcome
from app.decision_outcome_event_states import (
    CONTEXT_STATUSES, CONTRACT_VERSION, EVENT_STATES, NOT_ATTACHED,
    aggregate_decision_outcomes_by_event_state)
from app.decision_outcome_metrics import NOT_EVALUATED, aggregate_decision_outcomes
from app.news_event_context import EventContextPolicy, project_event_context
from app.paper.replay_models import PaperReplayBar
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision


AT = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
AS_OF = AT + timedelta(days=60)
ZERO = OutcomeAssumptions(assumptions_version='fixture-zero', market='EGX',
                          entry_slippage_bps=D('0'), exit_slippage_bps=D('0'),
                          cost_bps_per_side=D('0'))
POLICY = EventContextPolicy(policy_version='fixture-ctx-1', lookback_seconds=3 * 86400,
                            max_coverage_age_seconds=6 * 3600)
# Zero costs, R unit 1: WIN exits +2 on the next session, STP stops -1 on the fill session.
PATHS = {
    'WIN': [(0, '10', '10.6', '9.8', '10.4'), (1, '10.5', '12.3', '9.9', '12.1')],
    'STP': [(0, '10', '10.6', '8.9', '9.2')],
}
# Coverage age relative to detection: None -> UNAVAILABLE (no source configured),
# one hour -> NO_EVENT_FOUND (within the 6 h policy), one day -> STALE. No events
# are supplied; nothing here is a real news observation.
COVERAGE = {'UNAVAILABLE': None, 'NO_EVENT_FOUND': timedelta(hours=1),
            'STALE': timedelta(days=1)}


def day(n):
    return AT + timedelta(days=n)


def observation(symbol, number, offset, state=None, market='EGX'):
    at = day(offset)
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version='1',
                          config_id='fixture', symbol=symbol, decision_time=at,
                          data_cutoff=at - timedelta(minutes=1), state='READY',
                          entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    instrument = UUID(int=number)
    source = DecisionSource(market=market, symbol=symbol, instrument_id=instrument,
                            source_id='fixture', artifact_sha256='a' * 64,
                            evidence_ids=('fixture-only',), data_as_of=candidate.data_cutoff,
                            available_at=at, fresh_until=at + timedelta(hours=1),
                            completed_bar=True)
    terms = DecisionTerms(entry_trigger='LONG_ENTRY_ZONE_TOUCH_V1', entry_low=D('9.5'),
                          entry_high=D('10.5'), entry_reference=D('10'), stop=D('9'),
                          target=D('12'), valid_until=at + timedelta(days=3),
                          max_holding_sessions=3, flat_by=None)
    context = None
    if state is not None:
        age = COVERAGE[state]
        context = project_event_context((), market=market, instrument_id=instrument,
                                        decision_time=at, policy=POLICY,
                                        coverage_as_of=None if age is None else at - age)
        assert context.status == state
    record = decision_observation(TradeDecision(candidate=candidate, source=source, terms=terms),
                                  context, recorded_at=at + timedelta(minutes=5))
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
    plan = (('BARE', 1, 0, None, 'WIN'), ('NOEV', 2, 2, 'NO_EVENT_FOUND', 'STP'),
            ('OLD', 3, 4, 'STALE', 'WIN'), ('NOSRC', 4, 6, 'UNAVAILABLE', None),
            ('BARE2', 5, 8, None, 'STP'))
    observations, outcomes = {}, {}
    for symbol, number, offset, state, path in plan:
        observations[symbol] = observation(symbol, number, offset, state)
        if path is not None:
            outcomes[symbol] = outcome(observations[symbol], offset, path)
    return observations, outcomes


def by_state(population, outcomes, market='EGX', assumptions=ZERO, as_of=AS_OF):
    return aggregate_decision_outcomes_by_event_state(
        tuple(population), tuple(outcomes), market=market, assumptions=assumptions, as_of=as_of)


def states(result):
    return {entry['event_state']: entry for entry in result['states']}


def segment(entry):
    [only] = entry['metrics']['segments']
    return only


def forged(obs, **section_changes):
    """Re-address a record whose attached section was edited (digest kept consistent)."""
    _, record = obs
    record = deepcopy(record)
    section = record['event_context']['section']
    section.update(section_changes)
    record['event_context']['section_sha256'] = sha256(canonical_bytes(section)).hexdigest()
    return sha256(canonical_bytes(record)).hexdigest(), record


def test_states_are_aggregated_separately_without_headline():
    observations, outcomes = fixture()
    result = by_state(observations.values(), outcomes.values())
    assert result['contract_version'] == CONTRACT_VERSION == 'decision-outcome-event-state-metrics-v1'
    assert result['metrics_contract_version'] == 'decision-outcome-metrics-v1'
    assert result['section_contract_version'] == 'trade-card-event-context-v1'
    # Every state is reported, in fixed order, even when empty.
    assert result['event_states'] == list(EVENT_STATES) == [entry['event_state']
                                                            for entry in result['states']]
    assert EVENT_STATES == (NOT_ATTACHED, 'EVENT_FOUND', 'NO_EVENT_FOUND', 'STALE',
                            'UNAVAILABLE', 'AMBIGUOUS', 'UNKNOWN')
    assert result['state_counts'] == {'NOT_ATTACHED': 2, 'EVENT_FOUND': 0, 'NO_EVENT_FOUND': 1,
                                      'STALE': 1, 'UNAVAILABLE': 1, 'AMBIGUOUS': 0, 'UNKNOWN': 0}
    by = states(result)
    bare = segment(by[NOT_ATTACHED])
    assert (bare['observations'], bare['wins'], bare['losses'], D(bare['total_net_r'])) == (2, 1, 1, 1)
    noev = segment(by['NO_EVENT_FOUND'])
    assert (noev['wins'], noev['losses'], D(noev['total_net_r'])) == (0, 1, -1)
    stale = segment(by['STALE'])
    assert (stale['wins'], D(stale['total_net_r'])) == (1, 2)
    # A record without an outcome is NOT_EVALUATED in its own state, not dropped.
    nosrc = segment(by['UNAVAILABLE'])
    assert nosrc['status_counts'][NOT_EVALUATED] == 1 and nosrc['missing_data_rate'] == '1'
    for empty in ('EVENT_FOUND', 'AMBIGUOUS', 'UNKNOWN'):
        assert by[empty]['observations'] == 0
        assert by[empty]['metrics']['population_count'] == 0
        assert by[empty]['metrics']['segments'] == []
    assert sum(entry['observations'] for entry in result['states']) == 5
    assert sum(entry['metrics']['outcome_count'] for entry in result['states']) == 4
    whole = aggregate_decision_outcomes(tuple(observations.values()), tuple(outcomes.values()),
                                        market='EGX', assumptions=ZERO, as_of=AS_OF)
    assert (result['population_count'], result['outcome_count']) == (5, 4)
    assert result['population_sha256'] == whole['population_sha256']
    assert result['outcomes_sha256'] == whole['outcomes_sha256']
    # No cross-state number at the top level.
    assert result['cross_state_headline'] == 'NOT_COMPUTED'
    assert not {'segments', 'hit_rate', 'expectancy_net_r', 'max_drawdown_r'} & set(result)


def test_state_is_inference_without_risk_or_trading_effect():
    observations, outcomes = fixture()
    result = by_state(observations.values(), outcomes.values())
    assert result['state_basis'] == 'RETAINED_CARD_EVENT_CONTEXT_SECTION_AT_DETECTION_TIME'
    assert result['evidence_layer'] == 'NEWS_EVENT_INFERENCE'
    assert result['absence_is_proof'] is False
    assert (result['risk_interpretation'], result['trading_effect']) == ('NOT_EVALUATED', 'NONE')
    assert (result['sample_assessment'], result['performance_claim'], result['execution_evidence']) \
        == ('NOT_ASSESSED', 'NONE', 'NONE')
    assert (result['mode'], result['live_money'], result['execution_allowed'], result['promotion']) \
        == ('SHADOW', False, False, 'NOT_AUTHORIZED')
    for entry in result['states']:
        assert entry['metrics']['live_money'] is False
        assert entry['metrics']['cross_segment_headline'] == 'NOT_COMPUTED'
    assert not {'signal', 'order', 'side', 'quantity', 'risk', 'score'} & set(result)


def test_assignment_uses_the_retained_section_not_the_outcome():
    # Same path, different retained state: each lands in its own state only.
    win_bare = observation('A', 11, 0)
    win_stale = observation('B', 12, 0, 'STALE')
    result = by_state([win_bare, win_stale], [outcome(win_bare, 0, 'WIN'),
                                              outcome(win_stale, 0, 'WIN')])
    by = states(result)
    assert segment(by[NOT_ATTACHED])['wins'] == segment(by['STALE'])['wins'] == 1
    assert result['state_counts'][NOT_ATTACHED] == result['state_counts']['STALE'] == 1
    # The registered context statuses are exactly the EventContext statuses.
    assert set(CONTEXT_STATUSES) == set(EVENT_STATES) - {NOT_ATTACHED}


@pytest.mark.parametrize('changes, match', [
    ({'event_context_status': 'BULLISH'}, 'unregistered event_context_status'),
    ({'event_context_status': None}, 'unregistered event_context_status'),
    ({'contract_version': 'trade-card-event-context-v0'}, 'contract version'),
    ({'evidence_layer': 'OBSERVED_MARKET_FACT'}, 'inference'),
    ({'observed_market_fact': True}, 'inference'),
    ({'trading_effect': 'BUY'}, 'inference'),
    ({'market': 'US'}, 'market'),
    ({'instrument_id': str(UUID(int=99))}, 'instrument'),
])
def test_inconsistent_attached_section_fails_closed(changes, match):
    observations, outcomes = fixture()
    bad = forged(observations['NOEV'], **changes)
    population = dict(observations, NOEV=bad)
    rest = {key: value for key, value in outcomes.items() if key != 'NOEV'}
    with pytest.raises(ValueError, match=match):
        by_state(population.values(), rest.values())


def test_section_taken_after_detection_fails_closed():
    observations, outcomes = fixture()
    _, record = observations['OLD']
    later = (datetime.fromisoformat(record['detected_at'].replace('Z', '+00:00'))
             + timedelta(seconds=1)).isoformat()
    bad = forged(observations['OLD'], decision_time=later)
    rest = {key: value for key, value in outcomes.items() if key != 'OLD'}
    with pytest.raises(ValueError, match='detection time'):
        by_state(dict(observations, OLD=bad).values(), rest.values())
    # The same instant in another notation is accepted.
    same = (datetime.fromisoformat(record['detected_at'].replace('Z', '+00:00'))
            .astimezone(timezone(timedelta(hours=2))).isoformat())
    ok = forged(observations['OLD'], decision_time=same)
    result = by_state(dict(observations, OLD=ok).values(), rest.values())
    assert result['state_counts']['STALE'] == 1


def test_whole_input_still_fails_closed_before_partitioning():
    observations, outcomes = fixture()
    stray = deepcopy(outcomes['BARE'])
    stray['observation_identity'] = 'e' * 64
    with pytest.raises(ValueError, match='population observation'):
        by_state(observations.values(), list(outcomes.values()) + [stray])
    with pytest.raises(ValueError, match='more than one outcome'):
        by_state(observations.values(), list(outcomes.values()) + [outcomes['OLD']])
    us = observation('USX', 9, 10, market='US')
    with pytest.raises(ValueError, match='another market'):
        by_state(list(observations.values()) + [us], outcomes.values())
    edited = deepcopy(outcomes['NOEV'])
    edited['live_money'] = True
    with pytest.raises(ValueError, match='live_money'):
        by_state(observations.values(), dict(outcomes, NOEV=edited).values())
    # A section edited without re-addressing is caught by the content address.
    identity, record = observations['OLD']
    tampered = deepcopy(record)
    tampered['event_context']['section']['event_context_status'] = 'NO_EVENT_FOUND'
    with pytest.raises(ValueError):
        by_state(dict(observations, OLD=(identity, tampered)).values(), outcomes.values())
    with pytest.raises(ValueError, match='OutcomeAssumptions'):
        by_state(observations.values(), outcomes.values(),
                 assumptions=ZERO.model_dump(mode='python'))
    with pytest.raises(ValueError, match='timezone-aware'):
        by_state(observations.values(), outcomes.values(), as_of=AS_OF.replace(tzinfo=None))


def test_deterministic_and_order_independent():
    observations, outcomes = fixture()
    first = by_state(observations.values(), outcomes.values())
    again = by_state(reversed(list(observations.values())), reversed(list(outcomes.values())))
    assert canonical_bytes(first) == canonical_bytes(again)
    empty = by_state((), ())
    assert empty['population_count'] == 0
    assert [entry['observations'] for entry in empty['states']] == [0] * len(EVENT_STATES)
