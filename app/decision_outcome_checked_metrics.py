"""Aggregate EGX metrics over only outcomes attested CLEAR by ``as_of``.

Phase G/H/K groundwork (decision-outcome-checked-metrics-v1). The retained
decision-outcome-v1 record always says ``corporate_actions: NOT_CHECKED``, so
the unchanged ``aggregate_decision_outcomes`` cannot tell an outcome whose path
passed the corporate-action check from one that was never checked. This pure
module is the first caller of the attestation partition
(decision-outcome-attestation-partition-v1) for metrics: it composes, without
changing any of them,

1. ``aggregate_decision_outcomes`` over ALL supplied outcomes, used only as an
   integrity gate (market, exact assumptions, observation binding, population
   and outcome times by ``as_of``). Its result is discarded and never returned,
   so no reported metric includes an unchecked outcome;
2. ``partition_egx_attested_outcomes`` over the same inputs (every attestation
   re-verified with the unchanged verifier; none dropped);
3. a point-in-time rule CHECK_AS_OF_AT_OR_BEFORE_AS_OF_V1: an ATTESTED_CLEAR
   outcome is counted only when its attestation's ``check_as_of`` is at or
   before ``as_of``. A check made later is reported as
   ATTESTED_CLEAR_AFTER_AS_OF and not counted, so a later attestation cannot
   change an earlier report. The attestation's own retention time is not
   recorded by the sidecar store and is NOT checked;
4. ``aggregate_decision_outcomes`` again with the SAME population and only the
   counted outcomes. Every other population record (UNATTESTED,
   ATTESTATION_INVALID, attested after ``as_of``, or without an outcome) is
   counted NOT_EVALUATED there, so restricting to checked outcomes cannot hide a
   loss or shrink the sample.

The check itself is not re-run (its evidence document is not embedded), review
ids stay CALLER_ASSERTED_UNVERIFIED, and population and attestation
completeness are the caller's assertion. No IO, store read or write, producer or
job wiring, route or scheduler; nothing runs this module, so the SYSTEM gap
OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays open. No price is adjusted. Sample
size is exposed and not assessed. Outcomes are hypothetical results from session
bars: not fills, not performance. PAPER/SHADOW only; LIVE_MONEY=false.
"""
from hashlib import sha256

from app.decision_observation import _utc, canonical_bytes
from app.decision_observation_store import _time
from app.decision_outcome_attested import (
    attested_clear_outcomes, partition_egx_attested_outcomes)
from app.decision_outcome_metrics import _clock, aggregate_decision_outcomes

CONTRACT_VERSION = 'decision-outcome-checked-metrics-v1'
ATTESTATION_TIME_RULE = 'CHECK_AS_OF_AT_OR_BEFORE_AS_OF_V1'
COUNT_KEYS = ('ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF', 'ATTESTED_CLEAR_AFTER_AS_OF', 'UNATTESTED',
              'ATTESTATION_INVALID')


def select_egx_checked_outcomes(population, outcomes, attestations, *, assumptions,
                                as_of) -> dict:
    """Steps 1-3 above: integrity gate, re-verified partition and the CHECK_AS_OF rule.

    Returns ``as_of`` (normalised), ``partition``, ``counts`` (COUNT_KEYS),
    ``counted`` (the outcomes to aggregate, sorted by observation identity) and
    ``after`` (sorted outcome identities attested after ``as_of``). Shared by
    the report below and the checked per-period segmentation so the selection
    rule exists once. Pure; raises ``ValueError`` on any integrity failure.
    """
    # 1. Integrity gate over everything supplied; the result is never returned.
    aggregate_decision_outcomes(population, outcomes, market='EGX', assumptions=assumptions,
                                as_of=as_of)
    as_of = _clock(as_of, 'as_of')

    # 2. Re-verified partition (unchanged); every supplied outcome appears once.
    partition = partition_egx_attested_outcomes(population, outcomes, attestations)
    clear = {sha256(canonical_bytes(record)).hexdigest(): record
             for record in attested_clear_outcomes(population, outcomes, attestations)}

    # 3. Point-in-time: only checks made by as_of count.
    counts = {key: 0 for key in COUNT_KEYS}
    counted, after = [], []
    for entry in partition['entries']:
        status = entry['attestation_status']
        if status != 'ATTESTED_CLEAR':
            counts[status] += 1
        elif _time(entry['check_as_of'], 'check_as_of') > as_of:
            counts['ATTESTED_CLEAR_AFTER_AS_OF'] += 1
            after.append(entry['outcome_identity'])
        else:
            counts['ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF'] += 1
            counted.append(clear[entry['outcome_identity']])
    if sum(counts.values()) != partition['outcomes_supplied'] or len(clear) != (
            counts['ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF'] + counts['ATTESTED_CLEAR_AFTER_AS_OF']):
        raise ValueError('attestation partition does not account for every outcome')
    counted = tuple(sorted(counted, key=lambda record: record['observation_identity']))
    return {'as_of': as_of, 'partition': partition, 'counts': counts, 'counted': counted,
            'after': sorted(after)}


def aggregate_egx_checked_decision_outcomes(population, outcomes, attestations, *, assumptions,
                                            as_of) -> dict:
    """Return the JSON-ready decision-outcome-checked-metrics-v1 report; pure, fails closed.

    ``population`` is the retained ``(identity, record)`` EGX observation pairs,
    ``outcomes`` the retained terminal outcome records for ``assumptions`` and
    ``attestations`` the loaded attestation records. Raises ``ValueError`` on
    any integrity failure (nothing is silently dropped). Inputs are not mutated.
    """
    selection = select_egx_checked_outcomes(population, outcomes, attestations,
                                            assumptions=assumptions, as_of=as_of)
    as_of, partition, counted = selection['as_of'], selection['partition'], selection['counted']

    # 4. Same population, counted outcomes only: the rest are NOT_EVALUATED.
    metrics = aggregate_decision_outcomes(population, counted, market='EGX',
                                          assumptions=assumptions, as_of=as_of)
    return {
        'contract_version': CONTRACT_VERSION,
        'market': 'EGX',
        'as_of': _utc(as_of),
        'outcome_basis': 'ATTESTED_CLEAR_ONLY',
        'attestation_time_rule': ATTESTATION_TIME_RULE,
        'outcomes_supplied': partition['outcomes_supplied'],
        'attestations_supplied': partition['attestations_supplied'],
        'attestation_counts': selection['counts'],
        'outcomes_counted': len(counted),
        'outcomes_not_counted': partition['outcomes_supplied'] - len(counted),
        'not_counted_treatment': 'NOT_EVALUATED_IN_SAME_POPULATION_NEVER_DROPPED',
        'attested_after_as_of': selection['after'],
        'partition': partition,
        'metrics': metrics,
        'unrestricted_metrics': 'NOT_REPORTED',
        'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR',
        'absence_is_proof': False,
        'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'attestation_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'attestation_retention_time': 'NOT_RECORDED_NOT_CHECKED',
        'check_recomputation': 'NOT_PERFORMED',
        'review_verification': 'CALLER_ASSERTED_UNVERIFIED',
        'price_adjustment': 'NOT_APPLIED',
        'sample_assessment': 'NOT_ASSESSED',
        'cross_segment_headline': 'NOT_COMPUTED',
        'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED',
    }
