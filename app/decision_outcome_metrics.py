"""Pure segmented aggregate metrics over decision-outcome-v1 results.

Phase H/I groundwork. ``aggregate_decision_outcomes`` summarises, for ONE
market and ONE explicit ``OutcomeAssumptions``, a caller-supplied population of
retained decision-observation-v1 records (``(identity, record)`` pairs as
returned by ``load_retained_decision_observations``) and the decision-outcome-v1
results produced for them by ``evaluate_decision_outcome``.

Integrity (fail closed with ValueError):

- every population record is re-verified against its content address and must
  belong to the market and be recorded by ``as_of``; identities and decisions
  are unique;
- every outcome must name a population identity (at most one outcome each),
  carry the current contract/rules versions, the exact assumptions and the
  fixed safety labels, match its record's decision, instrument, strategy,
  detection time and entry trigger, be evaluated by ``as_of`` and be
  structurally consistent (registered status/reason, metrics iff CLOSED);
- population records without an outcome are counted as NOT_EVALUATED, so a
  loss cannot disappear by omitting its outcome. Population completeness itself
  is the caller's assertion and is labelled as such.

Outcome numbers are checked for structure and record binding but are NOT
recomputed (bars are not supplied here). Metrics are reported only per
segment (strategy id, strategy version, config id); there is no cross-market or
cross-version headline. Sample size is always exposed and not assessed for
sufficiency. Results are hypothetical outcomes from session bars, not fills and
not performance. No IO, store, route, scheduler or caller. PAPER/SHADOW only;
LIVE_MONEY=false.
"""
from datetime import date, datetime
from decimal import Context, Decimal, localcontext
from hashlib import sha256

from app.decision_observation import _utc, canonical_bytes
from app.decision_observation_store import _decimal, _time, read_retained_decision_observation
from app.decision_outcome import (
    CONTRACT_VERSION as OUTCOME_VERSION, REASONS, RULES, RULES_VERSION, OutcomeAssumptions)
from app.strategies.contracts import aware

CONTRACT_VERSION = 'decision-outcome-metrics-v1'
DEFINITIONS_VERSION = 'decision-outcome-metric-definitions-v1'
MAX_OBSERVATIONS = 10000
NOT_EVALUATED = 'NOT_EVALUATED'
STATUSES = ('CLOSED', 'NO_ENTRY', 'PENDING_PATH', 'UNEVALUABLE', NOT_EVALUATED)
EXIT_REASONS = ('STOP', 'TARGET', 'TIME_EXIT')
OUTCOME_FIELDS = frozenset((
    'contract_version', 'rules_version', 'rules', 'observation_identity', 'decision_id',
    'market', 'symbol', 'instrument_id', 'strategy_id', 'strategy_version', 'config_id',
    'entry_trigger', 'detected_at', 'path_complete_through_at', 'path_completeness',
    'evaluated_at', 'evaluated_at_clock', 'price_basis', 'corporate_actions', 'assumptions',
    'bars_supplied', 'bars_used', 'status', 'reason', 'entry', 'exit', 'metrics', 'fill_kind',
    'execution_evidence', 'performance_claim', 'mode', 'live_money', 'execution_allowed',
    'promotion'))
METRIC_FIELDS = frozenset((
    'r_unit_per_share', 'fill_risk_per_share', 'gross_r', 'net_r', 'cost_per_share', 'mae_r',
    'mfe_r', 'holding_sessions'))
FIXED_OUTCOME_VALUES = {
    'contract_version': OUTCOME_VERSION,
    'rules_version': RULES_VERSION,
    'path_completeness': 'CALLER_ASSERTED_UNVERIFIED',
    'evaluated_at_clock': 'CALLER_SUPPLIED_UNVERIFIED',
    'price_basis': 'RAW_UNADJUSTED_CALLER_ASSERTED',
    'corporate_actions': 'NOT_CHECKED',
    'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
    'execution_evidence': 'NONE',
    'performance_claim': 'NONE',
    'mode': 'SHADOW',
    'live_money': False,
    'execution_allowed': False,
    'promotion': 'NOT_AUTHORIZED',
}
BOUND_FIELDS = ('decision_id', 'market', 'symbol', 'instrument_id', 'strategy_id',
                'strategy_version', 'config_id', 'detected_at')
DEFINITIONS = {
    'segment': 'STRATEGY_ID_STRATEGY_VERSION_CONFIG_ID_WITHIN_ONE_MARKET_AND_ASSUMPTIONS',
    'observations': 'POPULATION_RECORDS_IN_SEGMENT_INCLUDING_NOT_EVALUATED',
    'win_loss': 'CLOSED_NET_R_GREATER_THAN_ZERO_WIN_LESS_THAN_ZERO_LOSS_ELSE_FLAT',
    'hit_rate': 'WINS_OVER_CLOSED',
    'expectancy_net_r': 'MEAN_NET_R_OVER_CLOSED',
    'payoff_ratio': 'MEAN_WIN_NET_R_OVER_ABS_MEAN_LOSS_NET_R',
    'missing_data_rate': 'UNEVALUABLE_PLUS_NOT_EVALUATED_OVER_OBSERVATIONS',
    'max_drawdown_r': 'PEAK_TO_TROUGH_CUMULATIVE_NET_R_ONE_R_PER_DECISION_'
                      'ORDERED_BY_EXIT_DATE_THEN_IDENTITY_NO_PORTFOLIO_CONSTRAINTS',
    'false_positive_rate': 'NOT_DEFINED',
    'undefined_ratio': 'NULL_WHEN_DENOMINATOR_IS_ZERO',
}


def _clock(value, name):
    if not isinstance(value, datetime):
        raise ValueError(f'timezone-aware {name} required')
    try:
        return aware(value)
    except ValueError as exc:
        raise ValueError(f'timezone-aware {name} required') from exc


def _population(population, market, as_of):
    if type(population) is not tuple or len(population) > MAX_OBSERVATIONS:
        raise ValueError(f'tuple of at most {MAX_OBSERVATIONS} (identity, record) pairs required')
    records, decisions = {}, set()
    for item in population:
        if type(item) is not tuple or len(item) != 2 or type(item[1]) is not dict:
            raise ValueError('(identity, record) pair required')
        identity, record = item
        try:
            payload = canonical_bytes(record)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError('record is not canonical JSON data') from exc
        record = read_retained_decision_observation(payload, expected_sha256=identity)
        if record['market'] != market:
            raise ValueError('population record from another market')
        if _time(record['recorded_at'], 'recorded_at') > as_of:
            raise ValueError('population record recorded after as_of')
        if identity in records or record['decision_id'] in decisions:
            raise ValueError('duplicate observation or decision in population')
        records[identity] = record
        decisions.add(record['decision_id'])
    return records


def _check_outcome(outcome, records, assumptions, as_of):
    if type(outcome) is not dict or set(outcome) != OUTCOME_FIELDS:
        raise ValueError('decision-outcome-v1 field set mismatch')
    for key, value in FIXED_OUTCOME_VALUES.items():
        if type(outcome[key]) is not type(value) or outcome[key] != value:
            raise ValueError(f'outcome {key} is not {value!r}')
    if outcome['rules'] != RULES:
        raise ValueError('outcome rules differ from the current rules')
    if outcome['assumptions'] != assumptions:
        raise ValueError('outcome assumptions differ from the supplied assumptions')
    identity = outcome['observation_identity']
    if type(identity) is not str or identity not in records:
        raise ValueError('outcome without a population observation')
    record = records[identity]
    for key in BOUND_FIELDS:
        if outcome[key] != record[key]:
            raise ValueError(f'outcome {key} does not match its observation')
    if outcome['entry_trigger'] != record['terms']['entry_trigger']:
        raise ValueError('outcome entry_trigger does not match its observation')
    complete = _time(outcome['path_complete_through_at'], 'path_complete_through_at')
    evaluated = _time(outcome['evaluated_at'], 'evaluated_at')
    if not complete <= evaluated <= as_of:
        raise ValueError('outcome evaluated after as_of or before its path')

    status, reason = outcome['status'], outcome['reason']
    if status not in REASONS or reason not in REASONS[status]:
        raise ValueError('unregistered outcome status or reason')
    if status != 'CLOSED':
        if outcome['metrics'] is not None or outcome['exit'] is not None:
            raise ValueError('only CLOSED outcomes carry exit and metrics')
        return identity, status, reason, None
    metrics, exit_ = outcome['metrics'], outcome['exit']
    if (type(metrics) is not dict or set(metrics) != METRIC_FIELDS
            or type(exit_) is not dict or type(outcome['entry']) is not dict):
        raise ValueError('CLOSED outcome requires entry, exit and metrics')
    if exit_.get('reason') not in EXIT_REASONS:
        raise ValueError('unregistered exit reason')
    try:
        exit_date = date.fromisoformat(exit_.get('market_date'))
    except (TypeError, ValueError) as exc:
        raise ValueError('exit market_date required') from exc
    holding = metrics['holding_sessions']
    if type(holding) is not int or holding < 1:
        raise ValueError('positive holding_sessions required')
    parsed = {key: _decimal(metrics[key], key) for key in ('gross_r', 'net_r', 'mae_r', 'mfe_r')}
    if parsed['mae_r'] > 0 or parsed['mfe_r'] < 0:
        raise ValueError('excursion signs inconsistent')
    parsed.update(holding_sessions=holding, exit_reason=exit_['reason'], exit_date=exit_date)
    return identity, status, reason, parsed


def _ratio(numerator, denominator):
    return None if denominator == 0 else str(Decimal(numerator) / Decimal(denominator))


def _mean(values):
    return None if not values else str(sum(values, Decimal(0)) / len(values))


def _segment(key, members):
    """members: list of (identity, record, status, reason, parsed)."""
    status_counts = {status: 0 for status in STATUSES}
    reason_counts, exit_counts = {}, {reason: 0 for reason in EXIT_REASONS}
    closed = []
    for identity, record, status, reason, parsed in members:
        status_counts[status] += 1
        if reason is not None:
            label = f'{status}:{reason}'
            reason_counts[label] = reason_counts.get(label, 0) + 1
        if parsed is not None:
            exit_counts[parsed['exit_reason']] += 1
            closed.append((parsed['exit_date'], identity, parsed))
    closed.sort(key=lambda item: (item[0], item[1]))
    net = [parsed['net_r'] for _, _, parsed in closed]
    wins = [value for value in net if value > 0]
    losses = [value for value in net if value < 0]
    cumulative = peak = drawdown = Decimal(0)
    for value in net:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    mean_win, mean_loss = _mean(wins), _mean(losses)
    detected = sorted(_time(record['detected_at'], 'detected_at') for _, record, *_ in members)
    holding = [parsed['holding_sessions'] for _, _, parsed in closed]
    count = len(members)
    return {
        'strategy_id': key[0],
        'strategy_version': key[1],
        'config_id': key[2],
        'observations': count,
        'status_counts': status_counts,
        'reason_counts': dict(sorted(reason_counts.items())),
        'closed': len(closed),
        'exit_reason_counts': exit_counts,
        'wins': len(wins),
        'losses': len(losses),
        'flat': len(net) - len(wins) - len(losses),
        'hit_rate': _ratio(len(wins), len(closed)),
        'mean_win_net_r': mean_win,
        'mean_loss_net_r': mean_loss,
        'payoff_ratio': None if mean_win is None or mean_loss is None
        else str(Decimal(mean_win) / abs(Decimal(mean_loss))),
        'expectancy_net_r': _mean(net),
        'expectancy_gross_r': _mean([parsed['gross_r'] for _, _, parsed in closed]),
        'total_net_r': str(sum(net, Decimal(0))),
        'max_drawdown_r': str(drawdown),
        'mean_mae_r': _mean([parsed['mae_r'] for _, _, parsed in closed]),
        'mean_mfe_r': _mean([parsed['mfe_r'] for _, _, parsed in closed]),
        'mean_holding_sessions': _mean([Decimal(value) for value in holding]),
        'max_holding_sessions': max(holding) if holding else None,
        'missing_data_rate': _ratio(
            status_counts['UNEVALUABLE'] + status_counts[NOT_EVALUATED], count),
        'detected_from': _utc(detected[0]),
        'detected_through': _utc(detected[-1]),
    }


def aggregate_decision_outcomes(population, outcomes, *, market, assumptions, as_of) -> dict:
    """JSON-ready decision-outcome-metrics-v1 for one market; pure."""
    if market not in ('EGX', 'US'):
        raise ValueError('unknown market')
    if type(assumptions) is not OutcomeAssumptions:
        raise ValueError('exact OutcomeAssumptions required')
    assumptions = OutcomeAssumptions.model_validate(assumptions.model_dump(mode='python'))
    if assumptions.market != market:
        raise ValueError('assumptions market mismatch')
    as_of = _clock(as_of, 'as_of')
    if type(outcomes) is not tuple or len(outcomes) > MAX_OBSERVATIONS:
        raise ValueError(f'tuple of at most {MAX_OBSERVATIONS} outcome dicts required')

    records = _population(population, market, as_of)
    expected = assumptions.model_dump(mode='json')
    checked = {}
    with localcontext(Context(prec=34)):
        for outcome in outcomes:
            identity, status, reason, parsed = _check_outcome(outcome, records, expected, as_of)
            if identity in checked:
                raise ValueError('more than one outcome for one observation')
            checked[identity] = (status, reason, parsed)

        groups = {}
        for identity in sorted(records):
            record = records[identity]
            status, reason, parsed = checked.get(identity, (NOT_EVALUATED, None, None))
            key = (record['strategy_id'], record['strategy_version'], record['config_id'])
            groups.setdefault(key, []).append((identity, record, status, reason, parsed))
        segments = [_segment(key, groups[key]) for key in sorted(groups)]

    ordered_outcomes = [outcome for _, outcome in sorted(
        (outcome['observation_identity'], outcome) for outcome in outcomes)]
    return {
        'contract_version': CONTRACT_VERSION,
        'definitions_version': DEFINITIONS_VERSION,
        'definitions': dict(DEFINITIONS),
        'outcome_contract_version': OUTCOME_VERSION,
        'rules_version': RULES_VERSION,
        'market': market,
        'as_of': _utc(as_of),
        'assumptions': expected,
        'population_count': len(records),
        'population_sha256': sha256(canonical_bytes(sorted(records))).hexdigest(),
        'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'outcome_count': len(checked),
        'not_evaluated_count': len(records) - len(checked),
        'outcomes_sha256': sha256(canonical_bytes(ordered_outcomes)).hexdigest(),
        'outcome_integrity': 'STRUCTURE_AND_OBSERVATION_BINDING_CHECKED_NOT_RECOMPUTED',
        'segments': segments,
        'cross_segment_headline': 'NOT_COMPUTED',
        'sample_assessment': 'NOT_ASSESSED',
        'evidence_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'promotion': 'NOT_AUTHORIZED',
    }
