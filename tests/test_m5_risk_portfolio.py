"""Offline admission acceptance; numbers are test fixtures, not validated policy."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain import MarketRegimeType, RiskDecisionType, SignalDirection, TradeState
from app.risk import RiskContext, RiskEngine, RiskPolicy
from app.strategies.contracts import Candidate
from test_risk_engine import make_context, make_plan, make_policy

D = Decimal


def inputs(**overrides):
    plan = make_plan()
    values = dict(plan=plan, context=make_context(), account_equity=D('70000'),
                  market_regime=MarketRegimeType.RISK_ON, decision_time=plan.created_at,
                  trade_state=TradeState.READY)
    values.update(overrides)
    return values


def evaluate(policy=None, **overrides):
    result = RiskEngine(policy if policy is not None else make_policy()).evaluate(**inputs(**overrides))
    assert result.approved_risk <= result.risk_budget
    if result.decision == RiskDecisionType.BLOCK:
        assert result.quantity == 0
        assert result.approved_risk == 0
        assert result.max_position_value == 0
    return result


@pytest.mark.parametrize('factory', [RiskPolicy, RiskContext, RiskEngine])
def test_explicit_construction_required(factory):
    with pytest.raises((TypeError, ValidationError)):
        factory()


@pytest.mark.parametrize('key', ['context', 'decision_time', 'trade_state'])
def test_explicit_evaluation_inputs(key):
    args = inputs()
    del args[key]
    with pytest.raises(TypeError):
        RiskEngine(make_policy()).evaluate(**args)


@pytest.mark.parametrize('value', [None, {}, 'policy'])
def test_policy_type_boundary(value):
    with pytest.raises(TypeError):
        RiskEngine(value)


@pytest.mark.parametrize('value', [None, {}, 'context'])
def test_context_type_boundary(value):
    with pytest.raises(TypeError):
        evaluate(context=value)


@pytest.mark.parametrize('field', list(RiskPolicy.model_fields))
def test_every_policy_parameter_required(field):
    values = make_policy().model_dump()
    del values[field]
    with pytest.raises(ValidationError):
        RiskPolicy(**values)


@pytest.mark.parametrize('changes', [
    {'risk_per_trade_pct': D('0')}, {'risk_per_trade_pct': D('.051')},
    {'max_position_pct': D('1.1')}, {'max_portfolio_exposure_pct': D('0')},
    {'max_portfolio_open_risk_pct': D('.005')}, {'max_symbol_exposure_pct': D('.7')},
    {'max_correlation_group_exposure_pct': D('.7')},
    {'max_correlation_group_exposure_pct': D('-.1')},
    {'max_open_positions': True}, {'max_open_positions': 0},
    {'daily_loss_limit_r': D('0')}, {'min_target1_r': D('-1')},
    {'risk_on_scale': D('.2'), 'neutral_scale': D('.5')},
    {'neutral_scale': D('1.1')}, {'risk_off_scale': D('.1')}, {'allow_short': 'false'},
    {'policy_version': ' '}, {'risk_on_scale': D('NaN')},
    {'daily_loss_limit_r': D('Infinity')}, {'max_position_pct': '.25'},
    {'max_position_pct': D('.7')}, {'unexpected': 1},
])
def test_invalid_policy(changes):
    with pytest.raises(ValidationError):
        make_policy(**changes)


@pytest.mark.parametrize('changes', [
    {'cash_balance': D('-1')}, {'reserved_cash': D('-1')},
    {'reserved_cash': D('70001')}, {'pending_entries': -1}, {'open_positions': True},
    {'pending_entries': 1.0}, {'current_open_risk_value': D('-1')},
    {'symbol_exposure_value': D('-1')}, {'current_exposure_value': D('-1')},
    {'liquidity_cap_value': D('-1')}, {'daily_realized_r': D('NaN')},
    {'cash_balance': D('Infinity')}, {'cash_balance': '70000'},
    {'symbol_exposure_value': D('1')}, {'correlation_group_id': 'banks'},
    {'correlation_group_exposure_value': D('0')},
    {'correlation_group_id': ' ', 'correlation_group_exposure_value': D('0')},
    {'correlation_group_id': 'banks', 'correlation_group_exposure_value': D('1')},
    {'unexpected': 1},
])
def test_invalid_context(changes):
    with pytest.raises(ValidationError):
        make_context(**changes)


@pytest.mark.parametrize('value', [None, '2026-09-10', datetime(2026, 9, 10)])
def test_aware_time_required(value):
    with pytest.raises(ValueError, match='timezone-aware'):
        evaluate(decision_time=value)


@pytest.mark.parametrize('offset,reason', [(-1, 'PLAN_NOT_YET_VALID'), (10800, 'PLAN_EXPIRED'), (10801, 'PLAN_EXPIRED')])
def test_plan_time_gate(offset, reason):
    plan = make_plan()
    result = evaluate(plan=plan, decision_time=plan.created_at + timedelta(seconds=offset))
    assert result.blockers == [reason]


def test_valid_time_boundaries_and_timezone_equivalence():
    plan = make_plan()
    for at in (plan.created_at, plan.valid_until - timedelta(microseconds=1),
               plan.created_at.astimezone(timezone.utc)):
        assert evaluate(plan=plan, decision_time=at).quantity == 700


@pytest.mark.parametrize('state', list(TradeState) + ['READY', 'bogus', None])
def test_lifecycle(state):
    result = evaluate(trade_state=state)
    if isinstance(state, TradeState) and state in (TradeState.READY, TradeState.ENTRY_TRIGGERED):
        assert result.quantity == 700
    else:
        assert result.blockers == ['INVALID_TRADE_STATE']


@pytest.mark.parametrize('opened,pending,blocked', [(2, 1, True), (0, 3, True), (1, 1, False), (3, 0, True)])
def test_concurrency(opened, pending, blocked):
    result = evaluate(context=make_context(open_positions=opened, pending_entries=pending))
    assert (result.decision == RiskDecisionType.BLOCK) == blocked


@pytest.mark.parametrize('cash,reserved,quantity', [('7000', '5000', 200), ('9', '0', 0), ('7000', '7000', 0)])
def test_cash(cash, reserved, quantity):
    result = evaluate(context=make_context(cash_balance=D(cash), reserved_cash=D(reserved)))
    assert result.quantity == quantity
    assert 'REDUCED_BY_CASH' in result.reasons


@pytest.mark.parametrize('name,field,used', [
    ('PORTFOLIO_OPEN_RISK', 'current_open_risk_value', '1900'),
    ('SYMBOL_EXPOSURE', 'symbol_exposure_value', '15500'),
    ('CORRELATION_GROUP_EXPOSURE', 'correlation_group_exposure_value', '15500'),
    ('PORTFOLIO_EXPOSURE', 'current_exposure_value', '40000'),
])
@pytest.mark.parametrize('quantity', [200, 0])
def test_portfolio_caps_reduce_and_block(name, field, used, quantity):
    risk = name == 'PORTFOLIO_OPEN_RISK'
    amount = D(used) + (D('200') if risk else D('2000')) * (quantity == 0)
    context = dict(current_exposure_value=D('40000'))
    context[field] = amount
    if name == 'CORRELATION_GROUP_EXPOSURE':
        context['correlation_group_id'] = 'group-a'
    policy = make_policy(max_correlation_group_exposure_pct=D('.25') if name == 'CORRELATION_GROUP_EXPOSURE' else None)
    result = evaluate(policy, context=make_context(**context))
    assert result.quantity == quantity
    assert result.quantity_caps[name] == quantity
    assert f'REDUCED_BY_{name}' in result.reasons


def test_missing_group_context_blocks_even_with_zero_cap():
    for cap in (D('0'), D('.25')):
        assert evaluate(make_policy(max_correlation_group_exposure_pct=cap)).blockers == ['MISSING_CORRELATION_GROUP_CONTEXT']
    result = evaluate(make_policy(max_correlation_group_exposure_pct=D('0')),
                      context=make_context(correlation_group_id='a', correlation_group_exposure_value=D('0')))
    assert result.quantity == 0


def test_all_caps_independent_and_smallest_wins():
    policy = make_policy(max_position_pct=D('.08'), max_correlation_group_exposure_pct=D('.25'))
    context = make_context(current_exposure_value=D('37000'), cash_balance=D('5000'),
                           reserved_cash=D('1000'), current_open_risk_value=D('1800'),
                           symbol_exposure_value=D('15500'), correlation_group_id='group-a',
                           correlation_group_exposure_value=D('16500'), liquidity_cap_value=D('500'))
    result = evaluate(policy, context=context)
    assert result.quantity_caps == dict(RISK_BUDGET=700, POSITION_CAP=560, PORTFOLIO_EXPOSURE=500,
                                       CASH=400, PORTFOLIO_OPEN_RISK=300, SYMBOL_EXPOSURE=200,
                                       CORRELATION_GROUP_EXPOSURE=100, LIQUIDITY=50)
    assert result.quantity == 50
    for name in result.quantity_caps:
        if name != 'RISK_BUDGET':
            assert f'REDUCED_BY_{name}' in result.reasons


@pytest.mark.parametrize('regime', [MarketRegimeType.RISK_OFF, MarketRegimeType.UNKNOWN, 'invalid'])
def test_blocking_regimes(regime):
    assert evaluate(market_regime=regime).blockers == ['MARKET_REGIME_BLOCK']


def test_short_control_and_valid_short_sizing():
    plan = make_plan().model_copy(update=dict(direction=SignalDirection.SHORT, stop_price=D('11'), target_1=D('8')))
    assert evaluate(plan=plan).blockers == ['SHORT_NOT_ALLOWED']
    assert evaluate(make_policy(allow_short=True), plan=plan).quantity == 700


def test_zero_liquidity_blocks():
    result = evaluate(context=make_context(liquidity_cap_value=D('0')))
    assert result.quantity == 0
    assert 'NO_CAPACITY_LIQUIDITY' in result.reasons


@pytest.mark.parametrize('value', [D('0'), D('-1'), D('NaN'), D('Infinity'), 70000])
def test_invalid_equity(value):
    with pytest.raises(ValueError):
        evaluate(account_equity=value)


def test_candidate_and_mapping_cannot_bypass_boundary():
    at = make_plan().created_at
    candidate = Candidate(strategy_id='SWING', strategy_version='1', config_id='fixture',
                          symbol='SWDY', decision_time=at, data_cutoff=at, state='READY',
                          entry_reference=10.0, stop_reference=9.0, evidence_json='{}')
    for value in (candidate, candidate.model_dump(), make_plan().model_dump(), None):
        with pytest.raises(TypeError, match='TradePlan'):
            evaluate(plan=value)


def test_revalidate_bypassed_models():
    with pytest.raises(ValidationError):
        evaluate(context=make_context().model_copy(update={'reserved_cash': D('70001')}))
    with pytest.raises(ValidationError):
        evaluate(plan=make_plan().model_copy(update={'stop_price': D('11')}))
    with pytest.warns(UserWarning, match='Pydantic serializer warnings'):
        with pytest.raises(ValidationError):
            RiskEngine(make_policy().model_copy(update={'allow_short': 'yes'}))


def test_policy_identity_and_deterministic_replay_without_mutation():
    policy = make_policy()
    assert policy.identity == make_policy(risk_per_trade_pct=D('.0100')).identity
    assert policy.identity != make_policy(policy_version='research-v2').identity
    for field, value in policy.model_dump().items():
        if isinstance(value, Decimal) and field != 'risk_off_scale':
            changed = policy.model_dump()
            changed[field] = value * D('.99')
            try:
                other = RiskPolicy(**changed)
            except ValidationError:
                continue
            assert policy.identity != other.identity
    args = inputs()
    snapshots = {k: v.model_dump() for k, v in args.items() if hasattr(v, 'model_dump')}
    engine = RiskEngine(policy)
    first, second = engine.evaluate(**args), engine.evaluate(**args)
    assert first.policy_version == policy.policy_version
    assert first.policy_identity == policy.identity
    assert first.model_dump(exclude={'risk_decision_id'}) == second.model_dump(exclude={'risk_decision_id'})
    assert snapshots == {k: v.model_dump() for k, v in args.items() if hasattr(v, 'model_dump')}
    with pytest.raises(ValidationError):
        policy.allow_short = True
    with pytest.raises(ValidationError):
        args['context'].reserved_cash = D('1')


def test_admission_creates_no_position_outcome_or_transition(monkeypatch):
    import app.domain as domain
    import app.domain.models as models

    def forbidden(*args, **kwargs):
        pytest.fail("risk admission must not create execution artifacts")

    for module in (domain, models):
        monkeypatch.setattr(module, "Position", forbidden)
        monkeypatch.setattr(module, "TradeOutcome", forbidden)
    args = inputs()
    before = args['plan'].model_dump()
    result = RiskEngine(make_policy()).evaluate(**args)
    assert isinstance(result, domain.RiskDecision)
    assert args['plan'].model_dump() == before
    assert args['trade_state'] is TradeState.READY


def test_fractional_share_risk_budget_blocks():
    result = evaluate(account_equity=D('1'))
    assert result.quantity == 0
    assert 'NO_CAPACITY_RISK_BUDGET' in result.reasons


@pytest.mark.parametrize('version', ['', ' ', '\t\n', '\u2003'])
def test_blank_policy_version_rejects(version):
    with pytest.raises(ValidationError):
        make_policy(policy_version=version)


@pytest.mark.parametrize('padding', [' ', '\t\n', '\u2003'])
def test_policy_version_canonicalization_and_identity(padding):
    policy = make_policy()
    padded = make_policy(policy_version=padding + policy.policy_version + padding)
    assert padded.policy_version == policy.policy_version
    assert padded.identity == policy.identity
    restored = RiskPolicy.model_validate_json(padded.model_dump_json())
    assert restored.identity == policy.identity
    result = evaluate(padded)
    assert result.policy_version == policy.policy_version
    assert result.policy_identity == policy.identity
