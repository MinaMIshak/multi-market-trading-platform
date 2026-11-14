"""Pure time-period segmentation of decision-outcome metrics (Phase I/J).

``aggregate_decision_outcomes_by_period`` partitions ONE market's retained
decision-observation-v1 population (and its decision-outcome-v1 results) into
the labelled periods of an explicit, versioned, caller-supplied
``EvaluationPeriodPolicy`` and reports the unchanged decision-outcome-metrics-v1
aggregate separately for each period. There is no cross-period headline:
development, validation, out-of-sample and forward PAPER/SHADOW observations
are never combined into one number here.

Assignment uses only each record's ``detected_at`` (the decision time) against
half-open ``[start, end)`` intervals, never the outcome, exit or evaluation
time, so a period cannot be conditioned on outcomes. Records detected outside
every period are counted as UNASSIGNED with an identity digest rather than
dropped silently.

Policy rules (fail closed with ValueError):

- labels DEVELOPMENT < VALIDATION < OUT_OF_SAMPLE < FORWARD_SHADOW appear at
  most once each, in that chronological order, with non-overlapping periods;
- ``registered_at`` is aware and not after ``as_of``; its clock is the
  caller's (CALLER_SUPPLIED_UNVERIFIED);
- FORWARD_SHADOW must start at or after ``registered_at`` (forward means
  observed after the period was fixed). Other periods report whether they were
  registered before they started; a holdout's integrity is the caller's
  assertion and is labelled as such.

The whole population/outcome input is first validated once by the unchanged
``aggregate_decision_outcomes``; each period is then aggregated by the same
function over its own subset. Nothing is recomputed from bars. No IO, store,
route, scheduler or caller. PAPER/SHADOW only; LIVE_MONEY=false.
"""
from datetime import datetime
from hashlib import sha256
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.decision_observation import _utc, canonical_bytes
from app.decision_observation_store import _time
from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_metrics import (
    CONTRACT_VERSION as METRICS_VERSION, _clock, aggregate_decision_outcomes)
from app.strategies.contracts import Contract, aware

CONTRACT_VERSION = 'decision-outcome-period-metrics-v1'
POLICY_SCHEMA = 'decision-outcome-period-policy-v1'
PERIOD_LABELS = ('DEVELOPMENT', 'VALIDATION', 'OUT_OF_SAMPLE', 'FORWARD_SHADOW')
UNASSIGNED = 'UNASSIGNED'
MAX_PERIODS = len(PERIOD_LABELS)


class EvaluationPeriod(Contract):
    """One labelled half-open detection-time interval ``[start, end)``."""

    label: Literal['DEVELOPMENT', 'VALIDATION', 'OUT_OF_SAMPLE', 'FORWARD_SHADOW']
    start: datetime
    end: datetime

    @field_validator('start', 'end')
    @classmethod
    def timezone_aware(cls, value):
        return aware(value)

    @model_validator(mode='after')
    def ordered(self):
        if not self.start < self.end:
            raise ValueError('period start must precede end')
        return self


class EvaluationPeriodPolicy(Contract):
    """Explicit, versioned per-market period labelling; declared by the caller."""

    schema_version: Literal['decision-outcome-period-policy-v1'] = POLICY_SCHEMA
    policy_version: str = Field(min_length=1, pattern=r'^\S+$')
    market: Literal['EGX', 'US']
    registered_at: datetime
    registration_clock: Literal['CALLER_SUPPLIED_UNVERIFIED'] = 'CALLER_SUPPLIED_UNVERIFIED'
    assignment_basis: Literal['DETECTED_AT_HALF_OPEN_INTERVAL'] = 'DETECTED_AT_HALF_OPEN_INTERVAL'
    periods: tuple[EvaluationPeriod, ...] = Field(min_length=1, max_length=MAX_PERIODS)

    @field_validator('registered_at')
    @classmethod
    def timezone_aware(cls, value):
        return aware(value)

    @model_validator(mode='after')
    def chronological(self):
        previous = None
        for period in self.periods:
            if previous is not None:
                if PERIOD_LABELS.index(period.label) <= PERIOD_LABELS.index(previous.label):
                    raise ValueError('period labels must be unique and in chronological order')
                if period.start < previous.end:
                    raise ValueError('periods must not overlap')
            previous = period
        for period in self.periods:
            if period.label == 'FORWARD_SHADOW' and period.start < self.registered_at:
                raise ValueError('FORWARD_SHADOW period must start at or after registered_at')
        return self


def _revalidated(policy):
    # Revalidate: model_copy/model_construct bypass checks.
    if type(policy) is not EvaluationPeriodPolicy or type(policy.periods) is not tuple or any(
            type(period) is not EvaluationPeriod for period in policy.periods):
        raise ValueError('exact EvaluationPeriodPolicy required')
    periods = tuple(EvaluationPeriod(**{name: getattr(period, name)
                                        for name in EvaluationPeriod.model_fields})
                    for period in policy.periods)
    values = {name: getattr(policy, name) for name in EvaluationPeriodPolicy.model_fields}
    return EvaluationPeriodPolicy(**dict(values, periods=periods))


def _digest(identities):
    return sha256(canonical_bytes(sorted(identities))).hexdigest()


def aggregate_decision_outcomes_by_period(population, outcomes, *, market, assumptions,
                                          as_of, policy) -> dict:
    """JSON-ready decision-outcome-period-metrics-v1 for one market; pure."""
    policy = _revalidated(policy)
    if type(assumptions) is not OutcomeAssumptions:
        raise ValueError('exact OutcomeAssumptions required')
    if policy.market != market:
        raise ValueError('period policy market mismatch')
    as_of = _clock(as_of, 'as_of')
    if policy.registered_at > as_of:
        raise ValueError('period policy registered after as_of')

    # Validates every record and outcome once (market, content address, binding,
    # as_of, duplicates) with the unchanged aggregate; fails closed on any error.
    whole = aggregate_decision_outcomes(population, outcomes, market=market,
                                        assumptions=assumptions, as_of=as_of)

    members = {index: [] for index in range(len(policy.periods))}
    unassigned = []
    assigned = {}
    for identity, record in population:
        detected = _time(record['detected_at'], 'detected_at')
        index = next((i for i, period in enumerate(policy.periods)
                      if period.start <= detected < period.end), None)
        assigned[identity] = index
        (unassigned if index is None else members[index]).append((identity, record))
    period_outcomes = {index: [] for index in members}
    unassigned_outcomes = 0
    for outcome in outcomes:
        index = assigned[outcome['observation_identity']]
        if index is None:
            unassigned_outcomes += 1
        else:
            period_outcomes[index].append(outcome)

    periods = []
    for index, period in enumerate(policy.periods):
        periods.append({
            'label': period.label,
            'start': _utc(period.start),
            'end': _utc(period.end),
            'end_reached_by_as_of': period.end <= as_of,
            'registration': ('REGISTERED_AT_OR_BEFORE_START' if policy.registered_at <= period.start
                             else 'REGISTERED_AFTER_START'),
            'metrics': aggregate_decision_outcomes(
                tuple(members[index]), tuple(period_outcomes[index]), market=market,
                assumptions=assumptions, as_of=as_of),
        })

    return {
        'contract_version': CONTRACT_VERSION,
        'metrics_contract_version': METRICS_VERSION,
        'market': market,
        'as_of': _utc(as_of),
        'policy': policy.model_dump(mode='json'),
        'policy_sha256': sha256(canonical_bytes(policy.model_dump(mode='json'))).hexdigest(),
        'assignment_basis': policy.assignment_basis,
        'population_count': whole['population_count'],
        'population_sha256': whole['population_sha256'],
        'outcome_count': whole['outcome_count'],
        'outcomes_sha256': whole['outcomes_sha256'],
        'population_completeness': whole['population_completeness'],
        'periods': periods,
        'unassigned': {
            'label': UNASSIGNED,
            'observations': len(unassigned),
            'outcomes': unassigned_outcomes,
            'identities_sha256': _digest(identity for identity, _ in unassigned),
        },
        'cross_period_headline': 'NOT_COMPUTED',
        'holdout_integrity': 'CALLER_ASSERTED_UNVERIFIED',
        'sample_assessment': 'NOT_ASSESSED',
        'evidence_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'promotion': 'NOT_AUTHORIZED',
    }
