"""Partition retained EGX outcomes by re-verified corporate-action attestation.

Phase G/K groundwork (decision-outcome-attestation-partition-v1). The retained
decision-outcome-v1 record always says ``corporate_actions: NOT_CHECKED``; the
sidecar attestation store (decision-outcome-attestation-retention-v1) holds, per
outcome, at most one decision-outcome-action-attestation-v1 record binding it to
the exact CLEAR check over its whole path. The store's loader checks structure
only. This pure module is the reader that turns those loaded records into a
per-outcome answer, without changing decision-outcome-v1, the outcome store, the
attestation store or the aggregate metrics.

Inputs are exactly what the unchanged loaders return: the observation
population ``((identity, record), ...)``, the outcomes ``(record, ...)`` and the
attestations ``(record, ...)``. Integrity (fail closed with ``ValueError``):

- every population record is re-read from its exact canonical bytes (EGX only);
  identities are unique;
- every outcome is re-read through the unchanged outcome-store reader at its own
  content address and re-bound to its population observation with the unchanged
  metrics check (an outcome without its observation is refused, not dropped);
  outcome identities are unique;
- every attestation is re-read through the unchanged attestation-store reader at
  its own content address; two attestations for one outcome are refused.

Then each outcome is exactly one of:

- ATTESTED_CLEAR: its attestation passes the unchanged
  ``verify_outcome_action_attestation`` against this outcome and observation;
- UNATTESTED: no attestation was supplied. This means NOT CHECKED, never clear;
- ATTESTATION_INVALID: an attestation names this outcome but does not verify
  against it (visible, never counted as attested).

No outcome is dropped: the counts always add up to the outcomes supplied.
Attestations naming an outcome that was not supplied (another assumptions set,
or no retained outcome) are listed separately, never matched by guesswork.
``attested_clear_outcomes`` returns only the ATTESTED_CLEAR records, ordered like
the outcome loader, for ``aggregate_decision_outcomes`` with the same
population: every other population record is then counted NOT_EVALUATED there,
so restricting to checked outcomes cannot hide a loss.

The check is not re-run (its evidence document is not embedded) and review ids
stay CALLER_ASSERTED_UNVERIFIED. No IO, store write, producer wiring, route or
scheduler; nothing calls this module yet, so the SYSTEM gap
OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED stays open. No price is adjusted.
Outcomes are hypothetical results from session bars: not fills, not
performance. PAPER/SHADOW only; LIVE_MONEY=false.
"""
from hashlib import sha256

from app.decision_observation import canonical_bytes
from app.decision_outcome_action_attestation import (
    _json, _observation, _outcome, verify_outcome_action_attestation)
from app.decision_outcome_attestation_store import read_retained_outcome_attestation

CONTRACT_VERSION = 'decision-outcome-attestation-partition-v1'
MAX_RECORDS = 10000
STATUSES = ('ATTESTED_CLEAR', 'UNATTESTED', 'ATTESTATION_INVALID')


def _tuple(value, name):
    if type(value) is not tuple or len(value) > MAX_RECORDS:
        raise ValueError(f'tuple of at most {MAX_RECORDS} {name} required')
    return value


def _population(population):
    records = {}
    for item in _tuple(population, 'observation pairs'):
        identity, record = _observation(item)
        if identity in records:
            raise ValueError('duplicate observation in population')
        records[identity] = record
    return records


def _outcomes(outcomes, records):
    held = {}
    for outcome in _tuple(outcomes, 'outcomes'):
        if type(outcome) is not dict:
            raise ValueError('decision-outcome-v1 dict required')
        payload, _ = _json(outcome, 'outcome')
        identity = sha256(payload).hexdigest()
        observation_identity = outcome.get('observation_identity')
        if type(observation_identity) is not str or observation_identity not in records:
            raise ValueError('outcome without a population observation')
        # Content address, terminal structure and binding to this observation.
        record = _outcome(identity, outcome, observation_identity, records[observation_identity])
        if identity in held:
            raise ValueError('duplicate outcome')
        held[identity] = record
    return held


def _attestations(attestations):
    by_outcome = {}
    for attestation in _tuple(attestations, 'attestations'):
        if type(attestation) is not dict:
            raise ValueError('decision-outcome-action-attestation-v1 dict required')
        payload, _ = _json(attestation, 'attestation')
        identity = sha256(payload).hexdigest()
        record = read_retained_outcome_attestation(payload, expected_sha256=identity)
        if record['outcome_identity'] in by_outcome:
            raise ValueError('outcome attested more than once')
        by_outcome[record['outcome_identity']] = (identity, record)
    return by_outcome


def _partition(population, outcomes, attestations):
    records = _population(population)
    held = _outcomes(outcomes, records)
    by_outcome = _attestations(attestations)
    entries = []
    for identity in sorted(held):
        outcome = held[identity]
        observation_identity = outcome['observation_identity']
        attestation_identity, check_as_of, check_timing = None, None, None
        found = by_outcome.get(identity)
        if found is None:
            status, reason = 'UNATTESTED', 'NO_ATTESTATION_SUPPLIED'
        else:
            try:
                verified = verify_outcome_action_attestation(
                    found[1], identity, outcome,
                    (observation_identity, records[observation_identity]))
            except (ValueError, TypeError, RecursionError):
                status, reason = 'ATTESTATION_INVALID', 'VERIFICATION_FAILED'
                attestation_identity = found[0]
            else:
                status, reason = 'ATTESTED_CLEAR', 'VERIFIED_CLEAR_CHECK_OVER_PATH'
                attestation_identity = found[0]
                check_as_of, check_timing = verified['check_as_of'], verified['check_timing']
        entries.append({
            'outcome_identity': identity,
            'observation_identity': observation_identity,
            'decision_id': outcome['decision_id'],
            'symbol': outcome['symbol'],
            'strategy_id': outcome['strategy_id'],
            'strategy_version': outcome['strategy_version'],
            'config_id': outcome['config_id'],
            'assumptions_version': outcome['assumptions']['assumptions_version'],
            'outcome_status': outcome['status'],
            'attestation_status': status,
            'reason': reason,
            'attestation_identity': attestation_identity,
            'check_as_of': check_as_of,
            'check_timing': check_timing,
        })
    unmatched = sorted(
        ({'attestation_identity': attestation_identity, 'outcome_identity': outcome_identity}
         for outcome_identity, (attestation_identity, _) in by_outcome.items()
         if outcome_identity not in held),
        key=lambda item: (item['outcome_identity'], item['attestation_identity']))
    return entries, unmatched, held


def partition_egx_attested_outcomes(population, outcomes, attestations) -> dict:
    """Return the decision-outcome-attestation-partition-v1 report; pure, fails closed.

    ``population`` is the retained ``(identity, record)`` observation pairs,
    ``outcomes`` the retained terminal outcome records and ``attestations`` the
    loaded attestation records. Every outcome appears exactly once in
    ``entries`` (ordered by outcome identity). Inputs are not mutated.
    """
    entries, unmatched, held = _partition(population, outcomes, attestations)
    counts = {status: 0 for status in STATUSES}
    for entry in entries:
        counts[entry['attestation_status']] += 1
    return {
        'contract_version': CONTRACT_VERSION,
        'market': 'EGX',
        'outcomes_supplied': len(held),
        'attestations_supplied': len(attestations),
        'counts': counts,
        'entries': entries,
        'attestations_without_supplied_outcome': unmatched,
        'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR',
        'absence_is_proof': False,
        'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
        'retained_outcome_corporate_actions': 'NOT_CHECKED_UNCHANGED',
        'check_recomputation': 'NOT_PERFORMED',
        'review_verification': 'CALLER_ASSERTED_UNVERIFIED',
        'price_adjustment': 'NOT_APPLIED',
        'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
        'execution_evidence': 'NONE',
        'performance_claim': 'NONE',
        'mode': 'SHADOW',
        'live_money': False,
        'execution_allowed': False,
        'runtime_activation': False,
        'promotion': 'NOT_AUTHORIZED',
    }


def attested_clear_outcomes(population, outcomes, attestations) -> tuple[dict, ...]:
    """Only the ATTESTED_CLEAR outcome records, ordered by observation identity.

    Pass them to ``aggregate_decision_outcomes`` with the same population: the
    other population records are then counted NOT_EVALUATED, never omitted.
    Returns fresh copies; fails closed exactly like the partition.
    """
    entries, _, held = _partition(population, outcomes, attestations)
    return tuple(sorted(
        (held[entry['outcome_identity']] for entry in entries
         if entry['attestation_status'] == 'ATTESTED_CLEAR'),
        key=lambda record: record['observation_identity']))
