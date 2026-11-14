"""Pure per-segment sample assessment under an explicit declared policy (Phase H/I).

``assess_decision_outcome_sample`` reports, for ONE market and ONE explicit
``OutcomeAssumptions``, whether each decision-outcome-metrics-v1 segment
(strategy id, strategy version, config id) meets the minimums of an explicit,
versioned, caller-supplied ``SampleAssessmentPolicy``:

- at least ``min_closed_outcomes`` CLOSED outcomes, and
- a missing-data rate (UNEVALUABLE plus NOT_EVALUATED over observations) of at
  most ``max_missing_data_rate``.

The thresholds are DECLARED_UNVALIDATED: meeting them says only that the
segment has the declared number of closed outcomes with a bounded missing-data
rate. It is not statistical significance, not evidence of edge, not empirical
validation and never promotion. No threshold is derived from outcomes here.

The metrics are computed by the unchanged ``aggregate_decision_outcomes`` over
the supplied population and outcomes (every existing fail-closed rule applies);
they are embedded unchanged so the assessment stays inspectable. Each segment
reports whether the policy was registered at or before its first detection, so
a threshold fixed after the data was seen is visible. There is no cross-segment
or cross-market headline. No IO, store, route, scheduler or caller.
PAPER/SHADOW only; LIVE_MONEY=false.
"""
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from pydantic import Field, field_validator

from app.decision_observation import _utc, canonical_bytes
from app.decision_observation_store import _time
from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_metrics import (
    CONTRACT_VERSION as METRICS_VERSION, MAX_OBSERVATIONS, _clock, aggregate_decision_outcomes)
from app.strategies.contracts import Contract, aware

CONTRACT_VERSION = 'decision-outcome-sample-assessment-v1'
POLICY_SCHEMA = 'decision-outcome-sample-policy-v1'
MEETS = 'MEETS_DECLARED_MINIMUM'
BELOW = 'BELOW_DECLARED_MINIMUM'
STATES = (MEETS, BELOW)
CLOSED_BELOW_MINIMUM = 'CLOSED_OUTCOMES_BELOW_POLICY_MINIMUM'
MISSING_ABOVE_MAXIMUM = 'MISSING_DATA_RATE_ABOVE_POLICY_MAXIMUM'
REASONS = (CLOSED_BELOW_MINIMUM, MISSING_ABOVE_MAXIMUM)


class SampleAssessmentPolicy(Contract):
    """Explicit, versioned per-market sample minimums; declared, not validated."""

    schema_version: Literal['decision-outcome-sample-policy-v1'] = POLICY_SCHEMA
    policy_version: str = Field(min_length=1, pattern=r'^\S+$')
    market: Literal['EGX', 'US']
    registered_at: datetime
    registration_clock: Literal['CALLER_SUPPLIED_UNVERIFIED'] = 'CALLER_SUPPLIED_UNVERIFIED'
    min_closed_outcomes: int = Field(ge=1, le=MAX_OBSERVATIONS)
    max_missing_data_rate: Decimal = Field(ge=0, le=1)
    basis: Literal['DECLARED_UNVALIDATED'] = 'DECLARED_UNVALIDATED'

    @field_validator('registered_at')
    @classmethod
    def timezone_aware(cls, value):
        return aware(value)

    @field_validator('max_missing_data_rate', mode='before')
    @classmethod
    def exact_decimal(cls, value):
        if type(value) is not Decimal or not value.is_finite():
            raise ValueError('finite exact Decimal required')
        return value


def _revalidated(policy):
    # Revalidate: model_copy/model_construct bypass checks.
    if type(policy) is not SampleAssessmentPolicy:
        raise ValueError('exact SampleAssessmentPolicy required')
    return SampleAssessmentPolicy(**{name: getattr(policy, name)
                                     for name in SampleAssessmentPolicy.model_fields})


def _assess(segment, policy):
    reasons = []
    if segment['closed'] < policy.min_closed_outcomes:
        reasons.append(CLOSED_BELOW_MINIMUM)
    # A segment always has at least one observation, so the rate is defined.
    if Decimal(segment['missing_data_rate']) > policy.max_missing_data_rate:
        reasons.append(MISSING_ABOVE_MAXIMUM)
    first = _time(segment['detected_from'], 'detected_from')
    return {
        'strategy_id': segment['strategy_id'],
        'strategy_version': segment['strategy_version'],
        'config_id': segment['config_id'],
        'observations': segment['observations'],
        'closed': segment['closed'],
        'missing_data_rate': segment['missing_data_rate'],
        'state': BELOW if reasons else MEETS,
        'reasons': reasons,
        'registration': ('REGISTERED_AT_OR_BEFORE_FIRST_DETECTION'
                         if policy.registered_at <= first
                         else 'REGISTERED_AFTER_FIRST_DETECTION'),
    }


def assess_decision_outcome_sample(population, outcomes, *, market, assumptions, as_of,
                                   policy) -> dict:
    """JSON-ready decision-outcome-sample-assessment-v1 for one market; pure."""
    policy = _revalidated(policy)
    if type(assumptions) is not OutcomeAssumptions:
        raise ValueError('exact OutcomeAssumptions required')
    if policy.market != market:
        raise ValueError('sample policy market mismatch')
    as_of = _clock(as_of, 'as_of')
    if policy.registered_at > as_of:
        raise ValueError('sample policy registered after as_of')

    metrics = aggregate_decision_outcomes(population, outcomes, market=market,
                                          assumptions=assumptions, as_of=as_of)
    segments = [_assess(segment, policy) for segment in metrics['segments']]
    state_counts = {state: 0 for state in STATES}
    for segment in segments:
        state_counts[segment['state']] += 1
    policy_json = policy.model_dump(mode='json')
    return {
        'contract_version': CONTRACT_VERSION,
        'metrics_contract_version': METRICS_VERSION,
        'market': market,
        'as_of': _utc(as_of),
        'policy': policy_json,
        'policy_sha256': sha256(canonical_bytes(policy_json)).hexdigest(),
        'population_count': metrics['population_count'],
        'population_sha256': metrics['population_sha256'],
        'outcome_count': metrics['outcome_count'],
        'outcomes_sha256': metrics['outcomes_sha256'],
        'population_completeness': metrics['population_completeness'],
        'segments': segments,
        'segment_state_counts': state_counts,
        'metrics': metrics,
        'sample_assessment': 'ASSESSED_AGAINST_DECLARED_UNVALIDATED_POLICY',
        'statistical_significance': 'NOT_ASSESSED',
        'empirical_validation': 'NOT_VALIDATED',
        'cross_segment_headline': 'NOT_COMPUTED',
        'evidence_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'promotion': 'NOT_AUTHORIZED',
    }
