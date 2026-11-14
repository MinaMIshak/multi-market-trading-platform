"""Pure event-risk-state segmentation of decision-outcome metrics (Phase I).

``aggregate_decision_outcomes_by_event_state`` partitions ONE market's retained
decision-observation-v1 population (and its decision-outcome-v1 results) by the
News/Event context state each record retained when it was recorded, and reports
the unchanged decision-outcome-metrics-v1 aggregate separately for each state:

- NOT_ATTACHED: the record carries no trade-card-event-context-v1 section;
- otherwise the attached section's ``event_context_status`` (EVENT_FOUND,
  NO_EVENT_FOUND, STALE, UNAVAILABLE, AMBIGUOUS, UNKNOWN).

Every state is always reported, empty or not, in a fixed order, so a state
cannot vanish from the report. There is no cross-state headline: records with
and without event context, or with different context states, are never
combined into one number here.

Assignment uses only the section retained inside the content-addressed record,
which the card section binds to the decision's detection time, never the
outcome, exit or evaluation time; it cannot be conditioned on outcomes or on
news retained later. Attached sections are additionally checked (fail closed
with ValueError) for the card-section contract version, inference layer,
market, canonical instrument, decision time equal to detection time, a
registered context status and no trading effect.

The state is inference, not an observed market fact; NO_EVENT_FOUND is not
proof that no relevant event existed, and no state is interpreted as risk or
used to accept, reject or rank anything. The whole input is first validated
once by the unchanged ``aggregate_decision_outcomes``; each state is then
aggregated by the same function over its own subset. Nothing is recomputed
from bars. No IO, store, route, scheduler or caller. PAPER/SHADOW only;
LIVE_MONEY=false.
"""
from typing import get_args

from app.decision_observation import _utc
from app.decision_observation_store import _time
from app.decision_outcome import OutcomeAssumptions
from app.decision_outcome_metrics import (
    CONTRACT_VERSION as METRICS_VERSION, _clock, aggregate_decision_outcomes)
from app.news_event_card import CONTRACT_VERSION as SECTION_VERSION
from app.news_event_context import EventContextStatus

CONTRACT_VERSION = 'decision-outcome-event-state-metrics-v1'
NOT_ATTACHED = 'NOT_ATTACHED'
CONTEXT_STATUSES = get_args(EventContextStatus)
EVENT_STATES = (NOT_ATTACHED,) + CONTEXT_STATUSES
STATE_BASIS = 'RETAINED_CARD_EVENT_CONTEXT_SECTION_AT_DETECTION_TIME'


def _event_state(record) -> str:
    context = record['event_context']
    if context['status'] == NOT_ATTACHED:
        return NOT_ATTACHED
    section = context['section']
    if section.get('contract_version') != SECTION_VERSION:
        raise ValueError('attached event_context section has an unknown contract version')
    if (section.get('evidence_layer') != 'NEWS_EVENT_INFERENCE'
            or section.get('observed_market_fact') is not False
            or section.get('trading_effect') != 'NONE'):
        raise ValueError('attached event_context section is not labelled inference')
    if section.get('market') != record['market']:
        raise ValueError('event_context section market differs from its observation')
    if section.get('instrument_id') != record['instrument_id']:
        raise ValueError('event_context section instrument differs from its observation')
    if _time(section.get('decision_time'), 'section decision_time') != _time(
            record['detected_at'], 'detected_at'):
        raise ValueError('event_context section not taken at the detection time')
    status = section.get('event_context_status')
    if type(status) is not str or status not in CONTEXT_STATUSES:
        raise ValueError('unregistered event_context_status')
    return status


def aggregate_decision_outcomes_by_event_state(population, outcomes, *, market, assumptions,
                                               as_of) -> dict:
    """JSON-ready decision-outcome-event-state-metrics-v1 for one market; pure."""
    if type(assumptions) is not OutcomeAssumptions:
        raise ValueError('exact OutcomeAssumptions required')
    as_of = _clock(as_of, 'as_of')

    # Validates every record and outcome once (market, content address, binding,
    # as_of, duplicates) with the unchanged aggregate; fails closed on any error.
    whole = aggregate_decision_outcomes(population, outcomes, market=market,
                                        assumptions=assumptions, as_of=as_of)

    members = {state: [] for state in EVENT_STATES}
    assigned = {}
    for identity, record in population:
        state = _event_state(record)
        assigned[identity] = state
        members[state].append((identity, record))
    state_outcomes = {state: [] for state in EVENT_STATES}
    for outcome in outcomes:
        state_outcomes[assigned[outcome['observation_identity']]].append(outcome)

    states = [{
        'event_state': state,
        'observations': len(members[state]),
        'metrics': aggregate_decision_outcomes(
            tuple(members[state]), tuple(state_outcomes[state]), market=market,
            assumptions=assumptions, as_of=as_of),
    } for state in EVENT_STATES]

    return {
        'contract_version': CONTRACT_VERSION,
        'metrics_contract_version': METRICS_VERSION,
        'section_contract_version': SECTION_VERSION,
        'market': market,
        'as_of': _utc(as_of),
        'state_basis': STATE_BASIS,
        'event_states': list(EVENT_STATES),
        'population_count': whole['population_count'],
        'population_sha256': whole['population_sha256'],
        'outcome_count': whole['outcome_count'],
        'outcomes_sha256': whole['outcomes_sha256'],
        'population_completeness': whole['population_completeness'],
        'states': states,
        'state_counts': {state: len(members[state]) for state in EVENT_STATES},
        'cross_state_headline': 'NOT_COMPUTED',
        'evidence_layer': 'NEWS_EVENT_INFERENCE',
        'absence_is_proof': False,
        'risk_interpretation': 'NOT_EVALUATED',
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
