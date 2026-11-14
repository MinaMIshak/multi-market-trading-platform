"""Pure data-age (freshness at detection) segmentation of decision-outcome metrics.

Phase I data-quality/freshness boundary: ``aggregate_decision_outcomes_by_data_age``
partitions ONE market's retained decision-observation-v1 population (and its
decision-outcome-v1 results) by how old the decision's input data was when the
decision was detected, and reports the unchanged decision-outcome-metrics-v1
aggregate separately for each age bucket of an explicit, versioned,
caller-supplied ``DataAgePolicy``.

Data age is ``detected_at - data_as_of`` in wall-clock seconds, read only from
the content-addressed record retained at detection time; it never uses the
outcome, exit or evaluation time, so a bucket cannot be conditioned on
outcomes. It is NOT session-aware: an EGX weekend or a U.S. holiday adds wall
clock age without adding missed sessions, and the result says so
(``age_basis``). Policy bounds are DECLARED_UNVALIDATED and carry no claim that
any age is acceptable or unacceptable.

Buckets for ascending bounds ``b1 < ... < bn`` (seconds, inclusive upper edges)
are ``AGE_LE_<b1>S``, ``AGE_GT_<b1>S_LE_<b2>S`` ... ``AGE_GT_<bn>S``. Every
bucket is always reported, empty or not, in that fixed order, so no bucket can
vanish and every record lands in exactly one bucket. There is no cross-bucket
headline.

Each record is additionally checked (fail closed with ValueError) for the
source-time order ``data_as_of <= available_at <= detected_at < fresh_until``
that ``TradeDecision`` enforces but the retained reader does not re-check, so
every assigned record was within its source-declared freshness at detection.

The whole input is first validated once by the unchanged
``aggregate_decision_outcomes``; each bucket is then aggregated by the same
function over its own subset. Nothing is recomputed from bars. No IO, store,
route, scheduler, caller or registered policy. PAPER/SHADOW only;
LIVE_MONEY=false.
"""
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.decision_observation import _utc, canonical_bytes
from app.decision_observation_store import _time
from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_metrics import (
    CONTRACT_VERSION as METRICS_VERSION, _clock, aggregate_decision_outcomes)
from app.strategies.contracts import Contract, aware

CONTRACT_VERSION = 'decision-outcome-data-age-metrics-v1'
POLICY_SCHEMA = 'decision-outcome-data-age-policy-v1'
AGE_BASIS = 'DETECTED_AT_MINUS_DATA_AS_OF_WALL_CLOCK_SECONDS_NOT_SESSION_AWARE'
MAX_BOUNDS = 8
MAX_BOUND_SECONDS = 366 * 86400


class DataAgePolicy(Contract):
    """Explicit, versioned per-market data-age bucket edges; declared, not validated."""

    schema_version: Literal['decision-outcome-data-age-policy-v1'] = POLICY_SCHEMA
    policy_version: str = Field(min_length=1, pattern=r'^\S+$')
    market: Literal['EGX', 'US']
    registered_at: datetime
    registration_clock: Literal['CALLER_SUPPLIED_UNVERIFIED'] = 'CALLER_SUPPLIED_UNVERIFIED'
    age_basis: Literal[
        'DETECTED_AT_MINUS_DATA_AS_OF_WALL_CLOCK_SECONDS_NOT_SESSION_AWARE'] = AGE_BASIS
    upper_bounds_seconds: tuple[int, ...] = Field(min_length=1, max_length=MAX_BOUNDS)
    basis: Literal['DECLARED_UNVALIDATED'] = 'DECLARED_UNVALIDATED'

    @field_validator('registered_at')
    @classmethod
    def timezone_aware(cls, value):
        return aware(value)

    @model_validator(mode='after')
    def ascending(self):
        previous = 0
        for bound in self.upper_bounds_seconds:
            if type(bound) is not int or not 0 < bound <= MAX_BOUND_SECONDS:
                raise ValueError('bounds must be integer seconds in (0, 366 days]')
            if bound <= previous:
                raise ValueError('bounds must be strictly ascending')
            previous = bound
        return self


def _revalidated(policy):
    # Revalidate: model_copy/model_construct bypass checks.
    if type(policy) is not DataAgePolicy or type(policy.upper_bounds_seconds) is not tuple:
        raise ValueError('exact DataAgePolicy required')
    return DataAgePolicy(**{name: getattr(policy, name) for name in DataAgePolicy.model_fields})


def bucket_labels(bounds) -> tuple:
    """Fixed-order labels for ascending inclusive upper edges (seconds)."""
    labels = [f'AGE_LE_{bounds[0]}S']
    labels += [f'AGE_GT_{low}S_LE_{high}S' for low, high in zip(bounds, bounds[1:])]
    labels.append(f'AGE_GT_{bounds[-1]}S')
    return tuple(labels)


def _data_age(record) -> timedelta:
    data_as_of = _time(record['data_as_of'], 'data_as_of')
    available = _time(record['available_at'], 'available_at')
    detected = _time(record['detected_at'], 'detected_at')
    fresh_until = _time(record['fresh_until'], 'fresh_until')
    if not data_as_of <= available <= detected < fresh_until:
        raise ValueError('record source times violate data_as_of <= available_at '
                         '<= detected_at < fresh_until')
    return detected - data_as_of


def _bucket(age: timedelta, bounds) -> int:
    for index, bound in enumerate(bounds):
        if age <= timedelta(seconds=bound):
            return index
    return len(bounds)


def aggregate_decision_outcomes_by_data_age(population, outcomes, *, market, assumptions,
                                            as_of, policy) -> dict:
    """JSON-ready decision-outcome-data-age-metrics-v1 for one market; pure."""
    policy = _revalidated(policy)
    if type(assumptions) is not OutcomeAssumptions:
        raise ValueError('exact OutcomeAssumptions required')
    if policy.market != market:
        raise ValueError('data-age policy market mismatch')
    as_of = _clock(as_of, 'as_of')
    if policy.registered_at > as_of:
        raise ValueError('data-age policy registered after as_of')

    # Validates every record and outcome once (market, content address, binding,
    # as_of, duplicates) with the unchanged aggregate; fails closed on any error.
    whole = aggregate_decision_outcomes(population, outcomes, market=market,
                                        assumptions=assumptions, as_of=as_of)

    bounds = policy.upper_bounds_seconds
    labels = bucket_labels(bounds)
    members = {index: [] for index in range(len(labels))}
    assigned = {}
    first_detection = None
    for identity, record in population:
        index = _bucket(_data_age(record), bounds)
        assigned[identity] = index
        members[index].append((identity, record))
        detected = _time(record['detected_at'], 'detected_at')
        if first_detection is None or detected < first_detection:
            first_detection = detected
    bucket_outcomes = {index: [] for index in members}
    for outcome in outcomes:
        bucket_outcomes[assigned[outcome['observation_identity']]].append(outcome)

    buckets = []
    for index, label in enumerate(labels):
        buckets.append({
            'label': label,
            'lower_exclusive_seconds': None if index == 0 else bounds[index - 1],
            'upper_inclusive_seconds': bounds[index] if index < len(bounds) else None,
            'observations': len(members[index]),
            'metrics': aggregate_decision_outcomes(
                tuple(members[index]), tuple(bucket_outcomes[index]), market=market,
                assumptions=assumptions, as_of=as_of),
        })

    if first_detection is None:
        registration = 'NO_OBSERVATIONS'
    elif policy.registered_at <= first_detection:
        registration = 'REGISTERED_AT_OR_BEFORE_FIRST_DETECTION'
    else:
        registration = 'REGISTERED_AFTER_FIRST_DETECTION'

    policy_json = policy.model_dump(mode='json')
    return {
        'contract_version': CONTRACT_VERSION,
        'metrics_contract_version': METRICS_VERSION,
        'market': market,
        'as_of': _utc(as_of),
        'policy': policy_json,
        'policy_sha256': sha256(canonical_bytes(policy_json)).hexdigest(),
        'policy_registration': registration,
        'age_basis': AGE_BASIS,
        'source_freshness_at_detection': 'WITHIN_SOURCE_DECLARED_FRESH_UNTIL',
        'bucket_labels': list(labels),
        'population_count': whole['population_count'],
        'population_sha256': whole['population_sha256'],
        'outcome_count': whole['outcome_count'],
        'outcomes_sha256': whole['outcomes_sha256'],
        'population_completeness': whole['population_completeness'],
        'buckets': buckets,
        'bucket_counts': {label: len(members[index]) for index, label in enumerate(labels)},
        'cross_bucket_headline': 'NOT_COMPUTED',
        'age_interpretation': 'NOT_EVALUATED',
        'trading_effect': 'NONE',
        'sample_assessment': 'NOT_ASSESSED',
        'evidence_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'promotion': 'NOT_AUTHORIZED',
    }
