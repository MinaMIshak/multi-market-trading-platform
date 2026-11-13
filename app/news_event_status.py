"""Code-declared News/Event Context and empirical-validation status for SYSTEM.

Constants only: no file, network or database IO, no store read, no event,
observation, outcome or metric computation, and no runtime observation. This
module deliberately imports none of the News/Event or decision-observation
modules; every declared module name and contract version is checked against the
code by tests/test_news_event_status.py, so a declaration cannot silently
outlive the change that alters it.

Stages are reported separately (code installed / configured / scheduled /
producing evidence / evidence current / empirically validated / promoted) so a
code-installed library is never presented as an operating capability. Counts
are null because nothing here observes a retained store; null is UNKNOWN, not
zero.

The milestone's EMPIRICAL_SAMPLE_INSUFFICIENT and EMPIRICAL_EVIDENCE_AVAILABLE
labels both claim an observed sample, and EMPIRICAL_COLLECTION_ACTIVE claims a
running producer; none exists, so the empirical state is
EMPIRICAL_COLLECTION_NOT_ACTIVE.
"""

SCHEMA_VERSION = 'news-empirical-feature-status-v1'

NEWS_STATUS = 'NEWS_CONTEXT_OFFLINE_ONLY'
NEWS_SOURCE_STATUS = 'NEWS_SOURCE_NOT_CONFIGURED'
EMPIRICAL_STATUS = 'EMPIRICAL_COLLECTION_NOT_ACTIVE'

# Each value is (module, contract/result version declared by that module).
NEWS_COMPONENTS = (
    ('app.news_event', 'news-event-v1'),
    ('app.news_event_context', 'news-event-context-v1'),
    ('app.news_event_record', 'news-event-v1'),
    ('app.news_event_store', 'news-event-retention-v1'),
    ('app.news_event_card', 'trade-card-event-context-v1'),
)
EMPIRICAL_COMPONENTS = (
    ('app.decision_observation', 'decision-observation-v1'),
    ('app.decision_observation_store', 'decision-observation-retention-v1'),
    ('app.decision_outcome', 'decision-outcome-v1'),
    ('app.decision_outcome_metrics', 'decision-outcome-metrics-v1'),
)

# Repository-level declarations. The stores take an explicit directory
# argument; there is no environment variable, provider, scheduler entry,
# route or caller for them in this repository.
NOT_CONFIGURED = 'NOT_CONFIGURED_IN_REPOSITORY'
NOT_SCHEDULED = 'NOT_SCHEDULED_IN_REPOSITORY'
NOT_OBSERVED = 'NOT_OBSERVED'

NEWS_GAPS = (
    'NEWS_SOURCE_PROVIDER_NOT_IMPLEMENTED',
    'NEWS_EVENT_STORE_NOT_PROVISIONED',
    'EVENT_CONTEXT_POLICY_NOT_OPERATIONAL',
    'CARD_EVENT_SECTION_NOT_IN_CARD_REPORT',
)
EMPIRICAL_GAPS = (
    'DECISION_OBSERVATION_PRODUCER_NOT_IMPLEMENTED',
    'DECISION_OBSERVATION_STORE_NOT_PROVISIONED',
    'SESSION_BAR_SOURCE_FOR_OUTCOMES_NOT_BOUND',
    'OUTCOME_RETENTION_NOT_IMPLEMENTED',
    'TIME_PERIOD_SEGMENTATION_NOT_IMPLEMENTED',
    'SAMPLE_SUFFICIENCY_POLICY_NOT_DEFINED',
)


def _stages(*, empirically_validated):
    return {
        'code_installed': 'CODE_INSTALLED',
        'configured': NOT_CONFIGURED,
        'scheduled': NOT_SCHEDULED,
        'producing_evidence': NOT_OBSERVED,
        'evidence_current': NOT_OBSERVED,
        'empirically_validated': empirically_validated,
        'promoted': 'NOT_AUTHORIZED',
    }


def feature_status():
    """Return a fresh JSON-ready code-declared status dict on every call."""
    return {
        'feature': 'NEWS_EVENT_CONTEXT_AND_EMPIRICAL_VALIDATION',
        'schema_version': SCHEMA_VERSION,
        'news_event_context': {
            'status': NEWS_STATUS,
            'source_status': NEWS_SOURCE_STATUS,
            'stages': _stages(empirically_validated='NOT_APPLICABLE'),
            'components': [{'module': module, 'version': version}
                           for module, version in NEWS_COMPONENTS],
            'gaps': list(NEWS_GAPS),
            'retained_events': None,
            'api_route': None,
            # TradeDecision.event_risk is Literal['UNKNOWN']; news cannot change it.
            'trade_decision_event_risk': 'UNKNOWN',
            'absence_is_proof': False,
            'trading_effect': 'NONE',
        },
        'empirical_validation': {
            'status': EMPIRICAL_STATUS,
            'stages': _stages(empirically_validated='NOT_VALIDATED'),
            'components': [{'module': module, 'version': version}
                           for module, version in EMPIRICAL_COMPONENTS],
            'gaps': list(EMPIRICAL_GAPS),
            'retained_observations': None,
            'evaluated_outcomes': None,
            'sample_assessment': 'NOT_ASSESSED',
            'cross_market_headline': 'NOT_COMPUTED',
            'performance_claim': 'NONE',
            'api_route': None,
        },
        'source': ('Code-declared constants; not a news fetch, store read, observation '
                   'count, outcome, metric, deployment or runtime observation'),
        'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
        'promotion': 'NOT_AUTHORIZED',
    }
