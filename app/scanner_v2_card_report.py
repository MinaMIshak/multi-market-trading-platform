"""Read-only Scanner v2 EGX Trade Card report: coverage + cards + provenance.

Offline projection only: no file, network or database IO, no HTTP route and no
runtime caller. It never accepts a caller-assembled decision dict. It re-runs
the existing retained-evidence binder on exact artifact bytes, then projects
each resulting trade-decision-v1 record through the existing ``trade_card``.

Nothing is admitted, risk-evaluated, filled or promoted. Cards are research
candidates (Candidate != fill). No lifecycle/performance artifact is bound, so
lifecycle evidence is reported as NOT_VERIFIED rather than inferred.
"""
from collections import Counter
from datetime import datetime, timezone
import json

from app.scanner_v2_retained_binding import (
    RetainedEgxRankBinding, bind_retained_egx_rank_report)
from app.strategies.contracts import aware
from app.strategies.trade_decision import TradeDecision, trade_card

SCHEMA_VERSION = 'scanner-v2-egx-card-report-v1'
FEATURE_STATUS = 'OFFLINE_PROJECTION_NOT_ACTIVATED'
# Read-only GET route serving only the protected loader's response. Until a
# trusted host producer publishes a report it answers NOT_CONFIGURED/UNAVAILABLE.
API_ROUTE = '/api/scanner-v2/egx/card-report'
# Read-only HTML view of the same loader response.
CARD_PAGE_ROUTE = '/scanner-v2/egx/cards'
# Host job CLI that can publish a report from retained evidence. Code-installed
# only: this repository holds no scheduler/cron entry for it, so it is reported
# as not scheduled and runtime_producer stays null until host evidence exists.
# A string, not an import, to avoid a job -> publisher -> report import cycle.
PRODUCER_ENTRYPOINT = 'python -m app.scanner_v2_card_report_job'
PRODUCER_SCHEDULE = 'NOT_SCHEDULED_IN_REPOSITORY'
# Host job CLI that retains the EGID identity and calendar observations the
# producer reads; it must run before EGX ranking generation for the session.
# Code-installed and unscheduled, exactly like PRODUCER_ENTRYPOINT; a string so
# this module imports no job code.
EVIDENCE_RETENTION_ENTRYPOINT = 'python -m app.scanner_v2_evidence_retention_job'
EVIDENCE_RETENTION_SCHEDULE = 'NOT_SCHEDULED_IN_REPOSITORY'
CARD_STATES = ('RESEARCH_CANDIDATE', 'DATA_STALE', 'PLAN_EXPIRED')
# Why the US adapter is NOT_IMPLEMENTED: inputs a trade-decision-v1 US binding
# needs that the repository does not provide. Each code is checked against the
# code by tests/test_scanner_v2_system_status.py; remove a code only together
# with the change that closes it. Not a runtime or data-coverage observation.
US_ADAPTER_GAPS = (
    # No US-RANK-v1 projection into trade-decision-v1 exists.
    'US_DECISION_ADAPTER_NOT_IMPLEMENTED',
    # us-ranking-report-v1 records carry ticker/ISIN only; the daily US pipeline
    # binds no admitted canonical instrument UUID.
    'US_INSTRUMENT_IDENTITY_NOT_BOUND',
    # Retained calendar observations project the EGX calendar only; the US
    # report's next session is an NYSE rule-calendar expectation, not retained.
    'US_CALENDAR_OBSERVATION_NOT_RETAINED',
    # The EGX adapter pins EGX-RANK-v1 rules/costs and Cairo session dates.
    'EGX_ADAPTER_RULES_NOT_REUSABLE_FOR_US',
)
LIMITATIONS = (
    'PUBLISHED_RANKING_SCOPE_NOT_AUTHORITATIVE_UNIVERSE',
    'IDENTITY_INTERNAL_SNAPSHOT_VERIFIED_ONLY',
    'CALENDAR_ADMISSION_NOT_ESTABLISHED',
    'RISK_NOT_EVALUATED',
    'CONFIDENCE_UNVALIDATED',
    'EVENT_RISK_UNKNOWN',
    'LIFECYCLE_EVIDENCE_NOT_VERIFIED',
    'RESEARCH_EVIDENCE_IS_NOT_EXPECTED_RETURN',
    'NO_RUNTIME_ACTIVATION',
)


def feature_status():
    """Code-declared Scanner v2 card-report status for SYSTEM visibility.

    Constants only: no IO, scan, card, deployment or runtime observation. The
    read-only API route and the host job entrypoints (evidence retention and
    card-report publication) are code-installed, but neither job is scheduled
    and no runtime producer has been observed, so counts of
    published cards stay null and the status stays not activated.
    Returns a fresh JSON-ready dict on every call.
    """
    return {
        'feature': 'SCANNER_V2_TRADE_CARD_REPORT',
        'schema_version': SCHEMA_VERSION,
        'status': FEATURE_STATUS,
        'market_adapters': {'EGX': 'OFFLINE_PROJECTION_ONLY', 'US': 'NOT_IMPLEMENTED'},
        'us_adapter_gaps': list(US_ADAPTER_GAPS),
        'api_route': API_ROUTE,
        'card_page': CARD_PAGE_ROUTE,
        'producer_entrypoint': PRODUCER_ENTRYPOINT,
        'producer_schedule': PRODUCER_SCHEDULE,
        'evidence_retention_entrypoint': EVIDENCE_RETENTION_ENTRYPOINT,
        'evidence_retention_schedule': EVIDENCE_RETENTION_SCHEDULE,
        'runtime_producer': None,
        'published_cards': None,
        'runtime_activation': False,
        'limitations': list(LIMITATIONS),
        'source': ('Code-declared constants; not a scan, candidate count, deployment '
                   'or runtime observation'),
        'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
        'promotion': 'NOT_AUTHORIZED',
    }


def _card_state(card):
    reason = card['invalidation_reason']
    if reason is None and card['status'] == 'RESEARCH_CANDIDATE':
        return 'RESEARCH_CANDIDATE'
    if reason in ('DATA_STALE', 'PLAN_EXPIRED') and card['status'] == 'EXCLUDED':
        return reason
    raise ValueError('unexpected trade card state')


def project_egx_card_report(artifact, *, expected_sha256, evidence, observed_at):
    """Project one retained-evidence EGX-RANK-v1 binding into a card report.

    ``observed_at`` is the viewer's time. It cannot precede ranking generation;
    cards viewed at/after the retained next-session open are labeled DATA_STALE
    by the existing card projection, never silently refreshed.
    """
    if type(evidence) is not RetainedEgxRankBinding:
        raise ValueError('RetainedEgxRankBinding required')
    now = aware(observed_at)
    result = bind_retained_egx_rank_report(artifact, expected_sha256=expected_sha256,
                                           evidence=evidence)
    if (result.get('market') != 'EGX' or result.get('live_money') is not False
            or result.get('execution_allowed') is not False
            or result.get('runtime_activation') is not False
            or result.get('promotion') != 'NOT_AUTHORIZED'):
        raise ValueError('invalid retained EGX decision result')
    if now < aware(datetime.fromisoformat(result['generated_at'])):
        raise ValueError('report observation precedes ranking generation')

    decisions = result['decisions']
    rows = result['symbols']
    candidate_rows = [r for r in rows if r['status'] == 'RESEARCH_CANDIDATE']
    if (type(decisions) is not tuple or len(decisions) != len(candidate_rows)
            or len(decisions) != result['candidate_count']
            or len(rows) != result['requested']
            or result['excluded'] != len(rows) - len(candidate_rows)
            or [r['symbol'] for r in rows] != sorted(r['symbol'] for r in rows)):
        raise ValueError('inconsistent EGX decision coverage')
    cards = []
    for row, decision in zip(candidate_rows, decisions):
        if (type(decision) is not TradeDecision or decision.source.market != 'EGX'
                or decision.source.symbol != row['symbol']
                or decision.identity != row['decision_id']):
            raise ValueError('decision does not match coverage row')
        cards.append(trade_card(decision, observed_at=now))
    states = Counter(_card_state(card) for card in cards)

    report = {
        'schema_version': SCHEMA_VERSION, 'market': 'EGX',
        'feature_status': FEATURE_STATUS,
        'observed_at': now.astimezone(timezone.utc).isoformat(),
        'coverage': {
            'scope_kind': result['scope_kind'],
            'authoritative_universe_size': None,
            'requested': result['requested'],
            'classification_counts': dict(result['classification_counts']),
            'candidate_count': result['candidate_count'],
            'excluded': result['excluded'],
            'exclusion_counts': dict(result['exclusion_counts']),
            'identity_exclusions': [dict(e) for e in result['identity_exclusions']],
            'symbols': [dict(r) for r in rows],
        },
        'provenance': {
            'artifact_sha256': result['artifact_sha256'],
            'adapter_version': result['adapter_version'],
            'rank_version': result['rank_version'], 'config_id': result['config_id'],
            'session': result['session'], 'generated_at': result['generated_at'],
            'evidence_available_at': result['evidence_available_at'],
            'binding_schema_version': result['binding_schema_version'],
            'freshness_policy': result['freshness_policy'],
            'identity_admission': result['identity_admission'],
            'calendar_admission': result['calendar_admission'],
            'retained_evidence': result['retained_evidence'],
        },
        'cards': cards,
        'card_state_counts': {s: states.get(s, 0) for s in CARD_STATES},
        'lifecycle_evidence': {'status': 'NOT_VERIFIED', 'sample_size': None,
                               'net_result': None},
        'limitations': list(LIMITATIONS),
        'research_status': 'UNVALIDATED', 'risk_status': 'NOT_EVALUATED',
        'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
        'runtime_activation': False, 'promotion': 'NOT_AUTHORIZED',
    }
    # Fail closed if anything non-JSON (e.g. a model object) leaked into output.
    json.dumps(report, allow_nan=False, sort_keys=True)
    return report
