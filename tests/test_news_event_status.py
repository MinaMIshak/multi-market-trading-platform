"""Code-declared News/Event + empirical SYSTEM status; not runtime or evidence."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import re
from typing import get_args
from unittest.mock import patch

from app.news_event_status import (
    EMPIRICAL_COMPONENTS, EMPIRICAL_GAPS, EMPIRICAL_STATUS, NEWS_COMPONENTS, NEWS_GAPS,
    NEWS_SOURCE_STATUS, NEWS_STATUS, NOT_CONFIGURED, NOT_OBSERVED, NOT_SCHEDULED,
    SCHEMA_VERSION, feature_status)
from app.ui.system import load_system_state, render_news_empirical, render_system

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = NEWS_COMPONENTS + EMPIRICAL_COMPONENTS
STAGE_KEYS = ('code_installed', 'configured', 'scheduled', 'producing_evidence',
              'evidence_current', 'empirically_validated', 'promoted')


def _path(module):
    return ROOT / (module.replace('.', '/') + '.py')


def test_feature_status_is_fixed_unobserved_and_fresh():
    status = feature_status()
    assert status['schema_version'] == SCHEMA_VERSION == 'news-empirical-feature-status-v1'
    news, empirical = status['news_event_context'], status['empirical_validation']
    assert news['status'] == NEWS_STATUS == 'NEWS_CONTEXT_OFFLINE_ONLY'
    assert news['source_status'] == NEWS_SOURCE_STATUS == 'NEWS_SOURCE_NOT_CONFIGURED'
    # No producer exists, so no label that claims an observed sample is used.
    assert empirical['status'] == EMPIRICAL_STATUS == 'EMPIRICAL_COLLECTION_NOT_ACTIVE'
    for part, validated in ((news, 'NOT_APPLICABLE'), (empirical, 'NOT_VALIDATED')):
        assert tuple(part['stages']) == STAGE_KEYS
        assert part['stages'] == {
            'code_installed': 'CODE_INSTALLED', 'configured': NOT_CONFIGURED,
            'scheduled': NOT_SCHEDULED, 'producing_evidence': NOT_OBSERVED,
            'evidence_current': NOT_OBSERVED, 'empirically_validated': validated,
            'promoted': 'NOT_AUTHORIZED'}
        assert part['api_route'] is None
    # Counts are UNKNOWN (null), never zero.
    assert news['retained_events'] is None
    assert empirical['retained_observations'] is None and empirical['evaluated_outcomes'] is None
    assert empirical['sample_assessment'] == 'NOT_ASSESSED'
    assert empirical['cross_market_headline'] == 'NOT_COMPUTED'
    assert empirical['performance_claim'] == 'NONE'
    assert news['trade_decision_event_risk'] == 'UNKNOWN'
    assert news['absence_is_proof'] is False and news['trading_effect'] == 'NONE'
    assert news['components'] == [{'module': m, 'version': v} for m, v in NEWS_COMPONENTS]
    assert empirical['components'] == [{'module': m, 'version': v} for m, v in EMPIRICAL_COMPONENTS]
    assert news['gaps'] == list(NEWS_GAPS) and empirical['gaps'] == list(EMPIRICAL_GAPS)
    assert status['live_money'] is status['execution_allowed'] is False
    assert status['mode'] == 'SHADOW' and status['promotion'] == 'NOT_AUTHORIZED'
    json.dumps(status, allow_nan=False)
    news['stages']['configured'] = 'MUTATED'
    news['gaps'].clear()
    empirical['components'].clear()
    fresh = feature_status()
    assert fresh['news_event_context']['stages']['configured'] == NOT_CONFIGURED
    assert fresh['news_event_context']['gaps'] == list(NEWS_GAPS)
    assert len(fresh['empirical_validation']['components']) == len(EMPIRICAL_COMPONENTS)


def test_declared_components_and_versions_match_the_code():
    from app import news_event
    for module_name, version in COMPONENTS:
        module = importlib.import_module(module_name)
        # Stores declare their result schema (one imports the record's CONTRACT_VERSION).
        attribute = 'RESULT_SCHEMA' if module_name.endswith('_store') else 'CONTRACT_VERSION'
        declared = getattr(module, attribute, None)
        if module_name == 'app.news_event_record':
            # The codec has no version of its own: it encodes news-event-v1 records.
            assert declared is None and module.NewsEvent is news_event.NewsEvent
            declared = news_event.CONTRACT_VERSION
        assert declared == version, module_name
    # The status module reads nothing: it imports no component (or anything else).
    source = (ROOT / 'app' / 'news_event_status.py').read_text(encoding='utf-8')
    assert not re.search(r'^\s*(import|from)\s', source, re.MULTILINE)


def test_declared_gaps_match_the_code():
    # No caller, producer, provider, route, card-report binding or scheduler:
    # outside the component modules, no app/ or tools/ source references one.
    # The status module names them only as strings (checked import-free above).
    allowed = {_path(module) for module, _ in COMPONENTS} | {ROOT / 'app' / 'news_event_status.py'}
    patterns = [re.compile(r'\b' + re.escape(module) + r'\b') for module, _ in COMPONENTS]
    patterns += [re.compile(r'from\s+app\s+import\s+[^\n]*\b' + re.escape(module.split('.')[1]) + r'\b')
                 for module, _ in COMPONENTS]
    sources = [p for base in ('app', 'tools') for p in (ROOT / base).rglob('*.py') if p.is_file()]
    assert ROOT / 'app' / 'main.py' in sources and ROOT / 'app' / 'core' / 'schedule.py' in sources
    for path in sources:
        if path in allowed:
            continue
        text = path.read_text(encoding='utf-8')
        for pattern in patterns:
            assert not pattern.search(text), (path, pattern.pattern)
    # NOT_CONFIGURED: the stores take explicit directories; no environment lookup.
    for module, _ in COMPONENTS:
        text = _path(module).read_text(encoding='utf-8')
        assert 'environ' not in text and 'getenv' not in text, module
    # The terminal-outcome store library exists (so its gap is replaced), but no
    # directory is provisioned and nothing calls it (no caller outside the
    # component modules, checked above). It never retains PENDING_PATH.
    assert 'OUTCOME_RETENTION_NOT_IMPLEMENTED' not in EMPIRICAL_GAPS
    assert 'OUTCOME_STORE_NOT_PROVISIONED' in EMPIRICAL_GAPS
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_store*.py')] == [
        'decision_outcome_store.py']
    outcome_store = (ROOT / 'app' / 'decision_outcome_store.py').read_text(encoding='utf-8')
    assert "TERMINAL_STATUSES = ('CLOSED', 'NO_ENTRY', 'UNEVALUABLE')" in outcome_store
    assert "'performance_claim': 'NONE'" in outcome_store
    assert "'promotion': 'NOT_AUTHORIZED'" in outcome_store
    assert feature_status()['empirical_validation']['evaluated_outcomes'] is None
    # The metrics module itself assesses nothing.
    metrics = (ROOT / 'app' / 'decision_outcome_metrics.py').read_text(encoding='utf-8')
    assert 'period' not in metrics and "'NOT_ASSESSED'" in metrics
    # EVALUATION_PERIOD_POLICY_NOT_REGISTERED: segmentation is a separate module
    # whose policy is caller-supplied; no repository policy instance exists, and
    # no caller exists (checked above). The period result is not a headline.
    assert 'TIME_PERIOD_SEGMENTATION_NOT_IMPLEMENTED' not in EMPIRICAL_GAPS
    periods = (ROOT / 'app' / 'decision_outcome_periods.py').read_text(encoding='utf-8')
    assert not re.search(r'^\w+\s*=\s*EvaluationPeriodPolicy\(', periods, re.MULTILINE)
    assert "'cross_period_headline': 'NOT_COMPUTED'" in periods
    # SAMPLE_ASSESSMENT_POLICY_NOT_REGISTERED: assessment is a separate module
    # whose declared policy is caller-supplied; no repository instance exists.
    assert 'SAMPLE_SUFFICIENCY_POLICY_NOT_DEFINED' not in EMPIRICAL_GAPS
    assert 'SAMPLE_ASSESSMENT_POLICY_NOT_REGISTERED' in EMPIRICAL_GAPS
    sample = (ROOT / 'app' / 'decision_outcome_sample.py').read_text(encoding='utf-8')
    assert not re.search(r'^\w+\s*=\s*SampleAssessmentPolicy\(', sample, re.MULTILINE)
    assert "'cross_segment_headline': 'NOT_COMPUTED'" in sample
    assert "'promotion': 'NOT_AUTHORIZED'" in sample
    # Event-state segmentation is a separate pure module: it reports each retained
    # event-context state separately, interprets no risk and has no headline.
    event_states = (ROOT / 'app' / 'decision_outcome_event_states.py').read_text(encoding='utf-8')
    assert "'cross_state_headline': 'NOT_COMPUTED'" in event_states
    assert "'risk_interpretation': 'NOT_EVALUATED'" in event_states
    assert "'trading_effect': 'NONE'" in event_states
    # Data-age segmentation is a separate pure module whose declared bucket
    # policy is caller-supplied; no repository instance exists and no headline.
    assert 'DATA_AGE_POLICY_NOT_REGISTERED' in EMPIRICAL_GAPS
    data_age = (ROOT / 'app' / 'decision_outcome_data_age.py').read_text(encoding='utf-8')
    assert not re.search(r'^\w+\s*=\s*DataAgePolicy\(', data_age, re.MULTILINE)
    assert "'cross_bucket_headline': 'NOT_COMPUTED'" in data_age
    assert "'age_interpretation': 'NOT_EVALUATED'" in data_age
    assert "'promotion': 'NOT_AUTHORIZED'" in data_age
    # The session-bar binding exists (so its gap is replaced); it evaluates and
    # retains nothing itself.
    assert 'SESSION_BAR_SOURCE_FOR_OUTCOMES_NOT_BOUND' not in EMPIRICAL_GAPS
    session_bars = (ROOT / 'app' / 'decision_outcome_session_bars.py').read_text(encoding='utf-8')
    assert 'evaluate_decision_outcome(' not in session_bars
    assert 'retain_decision_outcome' not in session_bars
    assert "'outcome': 'NOT_EVALUATED'" in session_bars
    assert "'corporate_actions': 'NOT_CHECKED'" in session_bars
    # The outcome producer library exists (so its gap is replaced) and only
    # composes the unchanged binding, evaluator and store; no job entrypoint,
    # scheduler or other source invokes it (no caller outside the component
    # modules, checked above), and corporate actions stay NOT_CHECKED.
    assert 'OUTCOME_PRODUCER_NOT_IMPLEMENTED' not in EMPIRICAL_GAPS
    assert 'OUTCOME_JOB_NOT_IMPLEMENTED' in EMPIRICAL_GAPS
    assert 'OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED' in EMPIRICAL_GAPS
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_producer*.py')] == [
        'decision_outcome_producer.py']
    assert not list((ROOT / 'app').glob('decision_outcome_job*.py'))
    outcome_producer = (ROOT / 'app' / 'decision_outcome_producer.py').read_text(encoding='utf-8')
    for call in ('bind_egx_outcome_session_bars(', 'evaluate_decision_outcome(',
                 'retain_decision_outcome(outcome_directory, (identity, record), outcome, now=now)',
                 'load_retained_decision_observations(', 'load_retained_decision_outcomes('):
        assert call in outcome_producer, call
    assert "'corporate_actions': 'NOT_CHECKED'" in outcome_producer
    assert "'performance_claim': 'NONE'" in outcome_producer
    assert "'promotion': 'NOT_AUTHORIZED'" in outcome_producer
    assert "'live_money': False" in outcome_producer
    # The daily-evidence loader exists but no job calls it (no caller outside
    # the component modules, checked above), so OUTCOME_JOB_NOT_IMPLEMENTED stays.
    # It reuses the unchanged ingestion lookup and canonical pipeline and never
    # writes the database.
    daily_evidence = (ROOT / 'app' / 'decision_outcome_daily_evidence.py').read_text(
        encoding='utf-8')
    assert 'get_manifest_by_raw_path(raw_path)' in daily_evidence
    assert 'canonicalize_ingestion(ingestion)' in daily_evidence
    assert not re.search(r'\b(INSERT|UPDATE|DELETE|initialize)\b', daily_evidence)
    assert "'corporate_actions': 'NOT_CHECKED'" in daily_evidence
    assert "'live_money': False" in daily_evidence
    # The observation producer library exists (so its gap is replaced) and
    # attaches no event context. Its host job entrypoint exists (so the job gap
    # is replaced), but no scheduler, cron entry or other source invokes the job
    # (no caller outside the component modules, checked above; app/core/schedule.py
    # is among the checked sources), so it stays NOT_SCHEDULED.
    assert 'DECISION_OBSERVATION_PRODUCER_NOT_IMPLEMENTED' not in EMPIRICAL_GAPS
    assert 'DECISION_OBSERVATION_JOB_NOT_IMPLEMENTED' not in EMPIRICAL_GAPS
    assert 'DECISION_OBSERVATION_JOB_NOT_SCHEDULED' in EMPIRICAL_GAPS
    assert feature_status()['empirical_validation']['stages']['scheduled'] == NOT_SCHEDULED
    producer = (ROOT / 'app' / 'decision_observation_producer.py').read_text(encoding='utf-8')
    assert "'event_context': 'NOT_ATTACHED'" in producer
    assert 'retain_decision_observation(directory, decision, None, now=now)' in producer
    assert "'performance_claim': 'NONE'" in producer and "'promotion': 'NOT_AUTHORIZED'" in producer
    assert [p.name for p in (ROOT / 'app').glob('decision_observation_job*.py')] == [
        'decision_observation_job.py']
    observation_job = (ROOT / 'app' / 'decision_observation_job.py').read_text(encoding='utf-8')
    # The job only wires the unchanged binding loader into the unchanged producer.
    assert 'produce_egx_decision_observations(\n        directory, artifact,' in observation_job
    assert 'load_retained_egx_rank_binding(' in observation_job
    assert "'performance_claim': 'NONE'" in observation_job
    assert "'promotion': 'NOT_AUTHORIZED'" in observation_job
    # SYSTEM keeps sample_assessment NOT_ASSESSED: nothing observes a sample.
    assert feature_status()['empirical_validation']['sample_assessment'] == 'NOT_ASSESSED'
    # News cannot change the decision's event risk.
    from app.strategies.trade_decision import TradeDecision
    field = TradeDecision.model_fields['event_risk']
    assert field.default == 'UNKNOWN' and get_args(field.annotation) == ('UNKNOWN',)


def test_system_state_and_page_expose_stages_without_counts():
    with patch.dict('os.environ', {}, clear=True):
        state = load_system_state()
    assert state['news_empirical'] == feature_status()
    assert state['live_money'] is False
    before = deepcopy(state)
    html = render_news_empirical(state)
    assert '<h2>News/Event and empirical validation</h2>' in html
    assert ('Status</th><td>NEWS_CONTEXT_OFFLINE_ONLY</td><td>EMPIRICAL_COLLECTION_NOT_ACTIVE'
            in html)
    assert 'News source</th><td>NEWS_SOURCE_NOT_CONFIGURED</td><td>NOT_APPLICABLE' in html
    assert ('Configured</th><td>NOT_CONFIGURED_IN_REPOSITORY</td>'
            '<td>NOT_CONFIGURED_IN_REPOSITORY') in html
    assert 'Producing evidence</th><td>NOT_OBSERVED</td><td>NOT_OBSERVED' in html
    assert 'Empirically validated</th><td>NOT_APPLICABLE</td><td>NOT_VALIDATED' in html
    assert 'Promoted</th><td>NOT_AUTHORIZED</td><td>NOT_AUTHORIZED' in html
    assert 'Retained events / observations</th><td>UNKNOWN</td><td>UNKNOWN' in html
    assert 'Evaluated outcomes</th><td>NOT_APPLICABLE</td><td>UNKNOWN' in html
    assert 'Trade decision event risk</th><td>UNKNOWN</td><td>NOT_APPLICABLE' in html
    assert 'API route</th><td>NOT_INSTALLED</td><td>NOT_INSTALLED' in html
    assert 'app.decision_outcome_metrics (decision-outcome-metrics-v1)' in html
    for gap in NEWS_GAPS + EMPIRICAL_GAPS:
        assert gap in html
    assert 'not proof' in html and 'LIVE_MONEY=DISABLED' in html
    assert 'BUY' not in html and 'SELL' not in html
    page = render_system(state)
    assert html in page and page.count('<h2>News/Event and empirical validation') == 1
    assert state == before


def test_render_escapes_and_degrades_to_unknown():
    for missing in ({}, {'news_empirical': 'x'}, {'news_empirical': None}):
        assert 'News/Event and empirical validation: UNKNOWN' in render_news_empirical(missing)
    forged = feature_status()
    forged['news_event_context']['status'] = '<script>x</script>'
    forged['news_event_context']['gaps'] = ['<b>x</b>']
    forged['empirical_validation']['components'] = [{'module': '<i>m</i>', 'version': 'v'}, 'x']
    html = render_news_empirical({'news_empirical': forged})
    assert '<script>' not in html and '&lt;script&gt;x&lt;/script&gt;' in html
    assert '<b>' not in html and '&lt;b&gt;x&lt;/b&gt;' in html
    assert '<i>' not in html and '&lt;i&gt;m&lt;/i&gt; (v), UNKNOWN' in html
    degraded = feature_status()
    degraded['news_event_context'] = None
    degraded['empirical_validation']['stages'] = 'x'
    degraded['empirical_validation']['gaps'] = []
    html = render_news_empirical({'news_empirical': degraded})
    assert 'Status</th><td>UNKNOWN</td><td>EMPIRICAL_COLLECTION_NOT_ACTIVE' in html
    assert 'Configured</th><td>UNKNOWN</td><td>UNKNOWN' in html
    assert 'Gaps</th><td>UNKNOWN</td><td>UNKNOWN' in html
    assert 'API route</th><td>NOT_INSTALLED</td><td>NOT_INSTALLED' in html
