"""Decision-observation producer for retained Scanner v2 EGX rankings.

Phase G collection step: turns the decisions of ONE manifest-verified
EGX-RANK-v1 ranking (the same retained artifact bytes and
``RetainedEgxRankBinding`` the card-report publisher uses) into write-once
decision-observation-v1 records in a host-provisioned observation store.

It only composes existing pieces; it adds no scanner, binding, record or store
logic:

1. ``bind_retained_egx_rank_report`` re-derives the exact ``TradeDecision``
   objects from the retained bytes (the card report does not retain decision
   objects, and caller-assembled decisions are never accepted);
2. the coverage is checked exactly as ``project_egx_card_report`` checks it
   (one decision per RESEARCH_CANDIDATE row, matching symbol and decision id);
3. for each decision whose recording window is open at the producer clock
   (detection <= now < fresh_until, valid_until and flat_by, the same window
   ``decision_observation`` enforces) the unchanged
   ``retain_decision_observation`` writes the record with ``recorded_at`` = the
   producer clock. No EventContext is attached: no news source or operational
   EventContextPolicy exists, so every record is event_context NOT_ATTACHED.

Decisions whose window is already closed are NOT retained and are reported
individually as WINDOW_CLOSED with their decision id, so a late run is visible
rather than silently shrinking the population; they are never back-filled with
a backdated clock. Excluded (non-candidate) rows are never observations and are
reported only as the binding's exclusion counts.

Failure: any store refusal raises ``DecisionObservationProductionError``
(RETENTION_FAILED with the store reason). Records retained earlier in the same
run stay (each is write-once and individually valid); a rerun inside the window
is idempotent (ALREADY_RETAINED keeps the first observation).

No network or database IO, no scheduler, job entrypoint, route or caller; the
store directory is an explicit argument. Records carry no outcome;
performance_claim NONE; nothing is admitted, filled or promoted; SHADOW;
LIVE_MONEY=false.
"""
from datetime import datetime, timezone
import os

from app.decision_observation_store import (
    DecisionObservationStoreError, retain_decision_observation)
from app.scanner_v2_retained_binding import (
    RetainedEgxRankBinding, bind_retained_egx_rank_report)
from app.strategies.contracts import aware
from app.strategies.trade_decision import TradeDecision

CONTRACT_VERSION = 'decision-observation-production-v1'
PRODUCTION_REASONS = (
    'CONFIG_INVALID', 'BINDING_REJECTED', 'CLOCK_BEFORE_GENERATION', 'RETENTION_FAILED')
DECISION_STATUSES = ('RETAINED', 'ALREADY_RETAINED', 'WINDOW_CLOSED')


class DecisionObservationProductionError(RuntimeError):
    """Production refused or failed; ``reason`` is one of PRODUCTION_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _bound_decisions(artifact, expected_sha256, evidence):
    """Exact decisions of one retained ranking with card-report coverage checks."""
    if type(evidence) is not RetainedEgxRankBinding:
        raise ValueError('RetainedEgxRankBinding required')
    result = bind_retained_egx_rank_report(artifact, expected_sha256=expected_sha256,
                                           evidence=evidence)
    if (result.get('market') != 'EGX' or result.get('live_money') is not False
            or result.get('execution_allowed') is not False
            or result.get('runtime_activation') is not False
            or result.get('promotion') != 'NOT_AUTHORIZED'):
        raise ValueError('invalid retained EGX decision result')
    decisions, rows = result['decisions'], result['symbols']
    candidate_rows = [r for r in rows if r['status'] == 'RESEARCH_CANDIDATE']
    if (type(decisions) is not tuple or len(decisions) != len(candidate_rows)
            or len(decisions) != result['candidate_count']
            or len(rows) != result['requested']
            or result['excluded'] != len(rows) - len(candidate_rows)):
        raise ValueError('inconsistent EGX decision coverage')
    for row, decision in zip(candidate_rows, decisions):
        if (type(decision) is not TradeDecision or decision.source.market != 'EGX'
                or decision.source.symbol != row['symbol']
                or decision.identity != row['decision_id']):
            raise ValueError('decision does not match coverage row')
    return result, decisions


def _window_open(decision, now):
    """Same recording window ``decision_observation`` enforces."""
    c, s, t = decision.candidate, decision.source, decision.terms
    if now < c.decision_time:
        return False
    return now < s.fresh_until and now < t.valid_until and (
        t.flat_by is None or now < t.flat_by)


def produce_egx_decision_observations(directory, artifact, *, expected_sha256, evidence,
                                      now=None):
    """Retain one observation per open-window decision of one retained ranking.

    ``now`` is the producer clock (defaults to real UTC); it becomes each new
    record's ``recorded_at``. Returns a ``decision-observation-production-v1``
    result listing every bound decision with status RETAINED, ALREADY_RETAINED
    or WINDOW_CLOSED. Refusals raise ``DecisionObservationProductionError``.
    """
    now = datetime.now(timezone.utc) if now is None else now
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise TypeError('now must be a timezone-aware datetime')
    if type(directory) is not str or not os.path.isabs(directory) or '\x00' in directory:
        raise DecisionObservationProductionError('CONFIG_INVALID')
    try:
        result, decisions = _bound_decisions(artifact, expected_sha256, evidence)
        generated = aware(datetime.fromisoformat(result['generated_at']))
    except (ValueError, TypeError, KeyError) as exc:
        raise DecisionObservationProductionError('BINDING_REJECTED', type(exc).__name__) from None
    if now < generated:
        raise DecisionObservationProductionError('CLOCK_BEFORE_GENERATION')

    entries = []
    for decision in decisions:
        entry = {'symbol': decision.source.symbol, 'decision_id': decision.identity,
                 'strategy_id': decision.candidate.strategy_id,
                 'strategy_version': decision.candidate.strategy_version,
                 'status': 'WINDOW_CLOSED', 'identity': None, 'recorded_at': None}
        if _window_open(decision, now):
            try:
                retained = retain_decision_observation(directory, decision, None, now=now)
            except DecisionObservationStoreError as exc:
                raise DecisionObservationProductionError(
                    'RETENTION_FAILED', f'{decision.source.symbol}: {exc.reason}') from None
            if (retained.get('decision_id') != decision.identity
                    or retained.get('status') not in ('RETAINED', 'ALREADY_RETAINED')):
                raise DecisionObservationProductionError('RETENTION_FAILED', 'unexpected result')
            entry.update(status=retained['status'], identity=retained['identity'],
                         recorded_at=retained['recorded_at'])
        entries.append(entry)

    return {
        'schema_version': CONTRACT_VERSION, 'market': 'EGX',
        'produced_at': _utc(now), 'produced_at_clock': 'PRODUCER_CLOCK_UNVERIFIED',
        'ranking_artifact_sha256': result['artifact_sha256'],
        'rank_version': result['rank_version'], 'config_id': result['config_id'],
        'session': result['session'], 'generated_at': result['generated_at'],
        'requested': result['requested'], 'candidate_count': result['candidate_count'],
        'excluded': result['excluded'], 'exclusion_counts': dict(result['exclusion_counts']),
        'decisions': entries,
        'status_counts': {s: sum(e['status'] == s for e in entries) for s in DECISION_STATUSES},
        'event_context_source': 'NOT_CONFIGURED', 'event_context': 'NOT_ATTACHED',
        'outcome_status': 'NOT_OBSERVED', 'performance_claim': 'NONE',
        'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
        'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED',
    }
