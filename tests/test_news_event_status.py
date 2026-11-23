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
    # composes the unchanged binding, evaluator and store; only its host job
    # entrypoint (a component) invokes it, and corporate actions stay NOT_CHECKED.
    assert 'OUTCOME_PRODUCER_NOT_IMPLEMENTED' not in EMPIRICAL_GAPS
    assert 'OUTCOME_CORPORATE_ACTIONS_NOT_CHECKED' in EMPIRICAL_GAPS
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_producer*.py')] == [
        'decision_outcome_producer.py']
    # The outcome job exists (so its gap is replaced), but no scheduler, cron
    # entry or other source invokes it (no caller outside the component modules,
    # checked above; app/core/schedule.py is among the checked sources). It only
    # wires the unchanged retained-calendar loader and chain (one job clock as
    # the chain's as_of and the producer clock), daily loader and producer,
    # writes no database row and never reads mutable market_sessions rows, so
    # the mutable-calendar gap is closed.
    assert 'OUTCOME_JOB_NOT_IMPLEMENTED' not in EMPIRICAL_GAPS
    assert 'OUTCOME_JOB_NOT_SCHEDULED' in EMPIRICAL_GAPS
    assert 'OUTCOME_CALENDAR_FROM_MUTABLE_SESSIONS' not in EMPIRICAL_GAPS
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_job*.py')] == [
        'decision_outcome_job.py']
    outcome_job = (ROOT / 'app' / 'decision_outcome_job.py').read_text(encoding='utf-8')
    for call in ('load_egx_calendar_observations(database, calendar_observations)',
                 "chain_egx_calendar_observations(calendar['observations'], as_of=now)",
                 'load_egx_daily_evidence(database, raw_root, daily_raw_paths)',
                 'produce_egx_decision_outcomes(\n        observation_directory, outcome_directory,',
                 "sessions=chained['sessions'], assumptions=assumptions, now=now, actions=actions,\n"
                 "        attestation_directory=attestation_directory,\n"
                 "        attest_held_outcomes=attest_held_outcomes)"):
        assert call in outcome_job, call
    assert not re.search(r'\b(INSERT|UPDATE|DELETE|initialize|save_market_session)\b', outcome_job)
    assert not re.search(r'\b(get_market_session|TradingRepository|FROM market_sessions)\b',
                         outcome_job)
    assert "CALENDAR_RETENTION = 'WRITE_ONCE_RETAINED'" in outcome_job
    assert "'admission': 'NOT_ESTABLISHED'" in outcome_job
    # The retained-calendar chain is a pure component: it re-validates each
    # retained observation with the unchanged scanner-path projection, reads no
    # database and admits nothing; its only caller is the outcome job.
    calendar_chain = (ROOT / 'app' / 'decision_outcome_calendar.py').read_text(encoding='utf-8')
    assert 'project_egx_calendar(' in calendar_chain
    assert not re.search(r'\b(connect|sqlite3|open|INSERT|UPDATE|DELETE)\b', calendar_chain)
    assert "'admission': 'NOT_ESTABLISHED'" in calendar_chain
    assert "'live_money': False" in calendar_chain
    # The retained-calendar loader reads caller-named calendar_observations rows
    # only (never market_sessions), writes nothing and admits nothing; its only
    # caller is the outcome job (a component, checked above).
    calendar_loader = (ROOT / 'app' / 'decision_outcome_calendar_loader.py').read_text(
        encoding='utf-8')
    assert 'FROM calendar_observations WHERE artifact_sha256 = ?' in calendar_loader
    assert not re.search(r'\b(INSERT|UPDATE|DELETE|initialize|save_market_session)\b',
                         calendar_loader)
    assert 'FROM market_sessions' not in calendar_loader
    assert "'admission': 'NOT_ESTABLISHED'" in calendar_loader
    assert "'live_money': False" in calendar_loader
    # The corporate-action path check is a pure component: it reads no database
    # or file, adjusts no price and evaluates no outcome; only components reference
    # it (the action-evidence loader, which does not call it, and the outcome
    # producer's optional gate, checked below), and the gap stays open.
    corporate_actions = (ROOT / 'app' / 'decision_outcome_corporate_actions.py').read_text(
        encoding='utf-8')
    assert not re.search(r'\b(connect|sqlite3|open|INSERT|UPDATE|DELETE|ReferenceRepository)\b',
                         corporate_actions.split('"""', 2)[2])
    assert "'price_adjustment': 'NOT_APPLIED'" in corporate_actions
    assert "'outcome': 'NOT_EVALUATED'" in corporate_actions
    assert "'review_verification': 'CALLER_ASSERTED_UNVERIFIED'" in corporate_actions
    assert "'live_money': False" in corporate_actions
    assert "'promotion': 'NOT_AUTHORIZED'" in corporate_actions
    # The action-evidence loader only delegates selection to the unchanged
    # ReferenceRepository.actions: no SQL of its own, no check call, no price
    # adjustment; its by-request form's only caller is the outcome job (a
    # component; no caller outside the components, checked above).
    action_evidence = (ROOT / 'app' / 'decision_outcome_action_evidence.py').read_text(
        encoding='utf-8').split('"""', 2)[2]
    assert 'repository.actions(' in action_evidence
    assert not re.search(r'\b(execute|SELECT|INSERT|UPDATE|DELETE|initialize)\b',
                         action_evidence)
    assert 'check_egx_outcome_corporate_actions' not in action_evidence
    assert "'corporate_action_check': 'NOT_PERFORMED_BY_LOADER'" in action_evidence
    assert "'price_adjustment': 'NOT_APPLIED'" in action_evidence
    assert "'database_writes': 'NONE_ON_SUCCESS'" in action_evidence
    assert "'live_money': False" in action_evidence
    assert "'promotion': 'NOT_AUTHORIZED'" in action_evidence
    # The outcome action attestation is a pure component: no IO, no store, no
    # price adjustment, no re-run of the check; nothing references it outside
    # the components (checked above), so the gap stays open.
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_action_attestation*.py')] == [
        'decision_outcome_action_attestation.py']
    attestation = (ROOT / 'app' / 'decision_outcome_action_attestation.py').read_text(
        encoding='utf-8').split('"""', 2)[2]
    assert not re.search(r'\b(connect|sqlite3|open|INSERT|UPDATE|DELETE|ReferenceRepository|'
                         r'retain_decision_outcome|check_egx_outcome_corporate_actions)\b',
                         attestation)
    assert "'corporate_action_status': 'CLEAR'" in attestation
    assert "'check_recomputation': 'NOT_PERFORMED'" in attestation
    assert "'price_adjustment': 'NOT_APPLIED'" in attestation
    assert "'performance_claim': 'NONE'" in attestation
    assert "'live_money': False" in attestation
    assert "'promotion': 'NOT_AUTHORIZED'" in attestation
    # Its only caller is the producer's opt-in attestation step
    # (decision-outcome-production-v3, checked below); the job does not reference
    # it (job-v4 only passes the directory through).
    assert 'decision_outcome_action_attestation' not in (
        ROOT / 'app' / 'decision_outcome_job.py').read_text(encoding='utf-8')
    # The attestation sidecar store library exists, but no directory is
    # provisioned and its only caller is the producer's opt-in step, which the
    # job enables only with an explicit attestation directory (the job does not
    # reference the store itself), so the corporate-action gap stays open. It retains
    # only re-verified attestations and never touches the outcome store or prices.
    assert 'OUTCOME_ATTESTATION_STORE_NOT_PROVISIONED' in EMPIRICAL_GAPS
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_attestation_store*.py')] == [
        'decision_outcome_attestation_store.py']
    attestation_store = (ROOT / 'app' / 'decision_outcome_attestation_store.py').read_text(
        encoding='utf-8')
    assert 'verify_outcome_action_attestation(\n        attestation,' in attestation_store
    assert not re.search(r'\b(connect|sqlite3|ReferenceRepository|retain_decision_outcome|'
                         r'check_egx_outcome_corporate_actions)\b',
                         attestation_store.split('"""', 2)[2])
    assert "'price_adjustment': 'NOT_APPLIED'" in attestation_store
    assert "'performance_claim': 'NONE'" in attestation_store
    assert "'live_money': False" in attestation_store
    assert "'promotion': 'NOT_AUTHORIZED'" in attestation_store
    assert 'decision_outcome_attestation_store' not in (
        ROOT / 'app' / 'decision_outcome_job.py').read_text(encoding='utf-8')
    # The attestation partition is a pure component: it re-verifies each loaded
    # attestation with the unchanged verifier, reads no file or database, writes
    # nothing and drops no outcome; its only caller is the pure checked-metrics
    # component (no caller outside the components, checked above), which nothing
    # runs, so the corporate-action gap stays open.
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_attested*.py')] == [
        'decision_outcome_attested.py']
    attested = (ROOT / 'app' / 'decision_outcome_attested.py').read_text(
        encoding='utf-8').split('"""', 2)[2]
    assert 'verify_outcome_action_attestation(\n                    found[1],' in attested
    assert not re.search(r'\b(connect|sqlite3|open|INSERT|UPDATE|DELETE|ReferenceRepository|'
                         r'retain_decision_outcome|retain_outcome_action_attestation|'
                         r'check_egx_outcome_corporate_actions|load_retained_\w+)\b', attested)
    assert "'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR'" in attested
    assert "'absence_is_proof': False" in attested
    assert "'price_adjustment': 'NOT_APPLIED'" in attested
    assert "'performance_claim': 'NONE'" in attested
    assert "'live_money': False" in attested
    assert "'promotion': 'NOT_AUTHORIZED'" in attested
    for source in (ROOT / 'app' / 'decision_outcome_producer.py',
                   ROOT / 'app' / 'decision_outcome_job.py'):
        assert 'decision_outcome_attested' not in source.read_text(encoding='utf-8')
    # The checked-metrics caller is pure: it composes the unchanged aggregate
    # (integrity gate, then counted outcomes only) and partition, reads no file,
    # store or database, writes nothing, and no job, producer or route calls it.
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_checked_metrics*.py')] == [
        'decision_outcome_checked_metrics.py']
    checked_metrics = (ROOT / 'app' / 'decision_outcome_checked_metrics.py').read_text(
        encoding='utf-8').split('"""', 2)[2]
    assert 'partition_egx_attested_outcomes(population, outcomes, attestations)' in checked_metrics
    assert len(re.findall(r'\baggregate_decision_outcomes\(', checked_metrics)) == 2
    assert not re.search(r'\b(connect|sqlite3|open|INSERT|UPDATE|DELETE|ReferenceRepository|'
                         r'retain_\w+|check_egx_outcome_corporate_actions|load_\w+)\b',
                         checked_metrics)
    assert "'unrestricted_metrics': 'NOT_REPORTED'" in checked_metrics
    assert "'absence_is_proof': False" in checked_metrics
    assert "'performance_claim': 'NONE'" in checked_metrics
    assert "'live_money': False" in checked_metrics
    assert "'promotion': 'NOT_AUTHORIZED'" in checked_metrics
    for source in (ROOT / 'app' / 'decision_outcome_producer.py',
                   ROOT / 'app' / 'decision_outcome_job.py'):
        assert 'decision_outcome_checked_metrics' not in source.read_text(encoding='utf-8')
    # decision-outcome-metrics-job-v1: the only caller of the checked-metrics
    # component is a read-only host job entrypoint. It loads the three stores
    # with the unchanged loaders, writes nothing, opens no database and is not
    # scheduled (no caller outside the components, checked above), so both
    # gaps stay open.
    assert 'OUTCOME_METRICS_JOB_NOT_SCHEDULED' in EMPIRICAL_GAPS
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_metrics_job*.py')] == [
        'decision_outcome_metrics_job.py']
    metrics_job = (ROOT / 'app' / 'decision_outcome_metrics_job.py').read_text(
        encoding='utf-8').split('"""', 2)[2]
    for call in ("load_retained_decision_observations(observation_directory, market='EGX')",
                 'load_retained_outcome_attestations(attestation_directory)',
                 'aggregate_egx_checked_decision_outcomes(\n            tuple(population),'):
        assert call in metrics_job, call
    assert not re.search(r'\b(connect|sqlite3|Database|_existing_database|INSERT|UPDATE|DELETE|'
                         r'retain_\w+|produce_\w+|aggregate_decision_outcomes|'
                         r'partition_egx_attested_outcomes|open)\b', metrics_job)
    assert "'writes': 'NONE'" in metrics_job
    assert "'performance_claim': 'NONE'" in metrics_job
    assert "'live_money': False" in metrics_job
    assert "'promotion': 'NOT_AUTHORIZED'" in metrics_job
    # decision-outcome-checked-period-metrics-v1: a pure composition of the
    # unchanged checked selection and period segmentation; it reads no file,
    # store or database, writes nothing, and no job (including the metrics job),
    # producer or route calls it (no caller outside the components, checked
    # above), so EVALUATION_PERIOD_POLICY_NOT_REGISTERED and the gap stay open.
    assert 'EVALUATION_PERIOD_POLICY_NOT_REGISTERED' in EMPIRICAL_GAPS
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_checked_periods*.py')] == [
        'decision_outcome_checked_periods.py']
    checked_periods = (ROOT / 'app' / 'decision_outcome_checked_periods.py').read_text(
        encoding='utf-8').split('"""', 2)[2]
    assert 'select_egx_checked_outcomes(population, outcomes, attestations,' in checked_periods
    assert 'aggregate_decision_outcomes_by_period(\n        population, counted,' in checked_periods
    assert not re.search(r'\b(connect|sqlite3|open|INSERT|UPDATE|DELETE|ReferenceRepository|'
                         r'retain_\w+|check_egx_outcome_corporate_actions|load_\w+|'
                         r'aggregate_decision_outcomes|partition_egx_attested_outcomes|'
                         r'EvaluationPeriodPolicy)\b', checked_periods)
    assert "'cross_period_headline': 'NOT_COMPUTED'" in checked_periods
    assert "'unrestricted_metrics': 'NOT_REPORTED'" in checked_periods
    assert "'performance_claim': 'NONE'" in checked_periods
    assert "'live_money': False" in checked_periods
    assert "'promotion': 'NOT_AUTHORIZED'" in checked_periods
    for source in (ROOT / 'app' / 'decision_outcome_metrics_job.py',
                   ROOT / 'app' / 'decision_outcome_job.py',
                   ROOT / 'app' / 'decision_outcome_producer.py'):
        assert 'decision_outcome_checked_periods' not in source.read_text(encoding='utf-8')
    # decision-outcome-checked-event-state-metrics-v1: a pure composition of the
    # unchanged checked selection and event-state segmentation; it reads no file,
    # store or database, writes nothing, interprets no risk, and no job, producer
    # or route calls it (no caller outside the components, checked above), so
    # no gap is closed or added.
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_checked_event_states*.py')] == [
        'decision_outcome_checked_event_states.py']
    checked_states = (ROOT / 'app' / 'decision_outcome_checked_event_states.py').read_text(
        encoding='utf-8').split('"""', 2)[2]
    assert 'select_egx_checked_outcomes(population, outcomes, attestations,' in checked_states
    assert 'aggregate_decision_outcomes_by_event_state(\n        population, counted,' in (
        checked_states)
    assert not re.search(r'\b(connect|sqlite3|open|INSERT|UPDATE|DELETE|ReferenceRepository|'
                         r'retain_\w+|check_egx_outcome_corporate_actions|load_\w+|'
                         r'aggregate_decision_outcomes|partition_egx_attested_outcomes|'
                         r'_event_state)\b', checked_states)
    assert "'cross_state_headline': 'NOT_COMPUTED'" in checked_states
    assert "'risk_interpretation': 'NOT_EVALUATED'" in checked_states
    assert "'trading_effect': 'NONE'" in checked_states
    assert "'unrestricted_metrics': 'NOT_REPORTED'" in checked_states
    assert "'performance_claim': 'NONE'" in checked_states
    assert "'live_money': False" in checked_states
    assert "'promotion': 'NOT_AUTHORIZED'" in checked_states
    for source in (ROOT / 'app' / 'decision_outcome_metrics_job.py',
                   ROOT / 'app' / 'decision_outcome_job.py',
                   ROOT / 'app' / 'decision_outcome_producer.py'):
        assert 'decision_outcome_checked_event_states' not in source.read_text(encoding='utf-8')
    # decision-outcome-checked-data-age-metrics-v1: a pure composition of the
    # unchanged checked selection and data-age segmentation; it reads no file,
    # store or database, writes nothing, constructs no policy, interprets no age,
    # and no job, producer or route calls it (no caller outside the components,
    # checked above), so DATA_AGE_POLICY_NOT_REGISTERED and the gap stay open.
    assert 'DATA_AGE_POLICY_NOT_REGISTERED' in EMPIRICAL_GAPS
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_checked_data_age*.py')] == [
        'decision_outcome_checked_data_age.py']
    checked_age = (ROOT / 'app' / 'decision_outcome_checked_data_age.py').read_text(
        encoding='utf-8').split('"""', 2)[2]
    assert 'select_egx_checked_outcomes(population, outcomes, attestations,' in checked_age
    assert 'aggregate_decision_outcomes_by_data_age(\n        population, counted,' in checked_age
    assert not re.search(r'\b(connect|sqlite3|open|INSERT|UPDATE|DELETE|ReferenceRepository|'
                         r'retain_\w+|check_egx_outcome_corporate_actions|load_\w+|'
                         r'aggregate_decision_outcomes|partition_egx_attested_outcomes|'
                         r'DataAgePolicy|_data_age|_bucket)\b', checked_age)
    assert "'cross_bucket_headline': 'NOT_COMPUTED'" in checked_age
    assert "'age_interpretation': 'NOT_EVALUATED'" in checked_age
    assert "'trading_effect': 'NONE'" in checked_age
    assert "'unrestricted_metrics': 'NOT_REPORTED'" in checked_age
    assert "'performance_claim': 'NONE'" in checked_age
    assert "'live_money': False" in checked_age
    assert "'promotion': 'NOT_AUTHORIZED'" in checked_age
    for source in (ROOT / 'app' / 'decision_outcome_metrics_job.py',
                   ROOT / 'app' / 'decision_outcome_job.py',
                   ROOT / 'app' / 'decision_outcome_producer.py'):
        assert 'decision_outcome_checked_data_age' not in source.read_text(encoding='utf-8')
    # decision-outcome-checked-sample-assessment-v1: a pure composition of the
    # unchanged checked selection and sample assessment; it reads no file, store
    # or database, writes nothing, constructs no policy, and no job, producer or
    # route calls it (no caller outside the components, checked above), so
    # SAMPLE_ASSESSMENT_POLICY_NOT_REGISTERED and the gap stay open.
    assert 'SAMPLE_ASSESSMENT_POLICY_NOT_REGISTERED' in EMPIRICAL_GAPS
    assert [p.name for p in (ROOT / 'app').glob('decision_outcome_checked_sample*.py')] == [
        'decision_outcome_checked_sample.py']
    checked_sample = (ROOT / 'app' / 'decision_outcome_checked_sample.py').read_text(
        encoding='utf-8').split('"""', 2)[2]
    assert 'select_egx_checked_outcomes(population, outcomes, attestations,' in checked_sample
    assert 'assess_decision_outcome_sample(\n        population, counted,' in checked_sample
    assert not re.search(r'\b(connect|sqlite3|open|INSERT|UPDATE|DELETE|ReferenceRepository|'
                         r'retain_\w+|check_egx_outcome_corporate_actions|load_\w+|'
                         r'aggregate_decision_outcomes|partition_egx_attested_outcomes|'
                         r'SampleAssessmentPolicy|_assess|_revalidated)\b', checked_sample)
    assert "'cross_segment_headline': 'NOT_COMPUTED'" in checked_sample
    assert "'statistical_significance': 'NOT_ASSESSED'" in checked_sample
    assert "'empirical_validation': 'NOT_VALIDATED'" in checked_sample
    assert "'unrestricted_assessment': 'NOT_REPORTED'" in checked_sample
    assert "'performance_claim': 'NONE'" in checked_sample
    assert "'live_money': False" in checked_sample
    assert "'promotion': 'NOT_AUTHORIZED'" in checked_sample
    for source in (ROOT / 'app' / 'decision_outcome_metrics_job.py',
                   ROOT / 'app' / 'decision_outcome_job.py',
                   ROOT / 'app' / 'decision_outcome_producer.py'):
        assert 'decision_outcome_checked_sample' not in source.read_text(encoding='utf-8')
    assert "'corporate_actions': 'NOT_CHECKED'" in outcome_job
    assert "'performance_claim': 'NONE'" in outcome_job
    assert "'promotion': 'NOT_AUTHORIZED'" in outcome_job
    assert "'live_money': False" in outcome_job
    outcome_producer = (ROOT / 'app' / 'decision_outcome_producer.py').read_text(encoding='utf-8')
    for call in ('bind_egx_outcome_session_bars(', 'evaluate_decision_outcome(',
                 'retain_decision_outcome(outcome_directory, (identity, record), outcome, now=now)',
                 'load_retained_decision_observations(', 'load_retained_decision_outcomes('):
        assert call in outcome_producer, call
    # decision-outcome-production-v2: the unchanged pure check gates retention
    # only when the caller supplies selections; otherwise the run says NOT_CHECKED.
    # The retained outcome record does not carry the check, so the gap stays open
    # (asserted above) even though decision-outcome-job-v3 can pass selections.
    assert ("'corporate_actions': 'NOT_CHECKED' if actions is None else "
            "'CHECKED_BEFORE_RETENTION'") in outcome_producer
    assert 'check_egx_outcome_corporate_actions(' in outcome_producer
    assert "'retained_outcome_corporate_actions': 'NOT_RECORDED_IN_OUTCOME'" in outcome_producer
    assert "'price_adjustment': 'NOT_APPLIED'" in outcome_producer
    # decision-outcome-production-v3: only with an opt-in attestation directory
    # (which requires selections), an outcome retained after a CLEAR check is
    # attested with the unchanged builder and sidecar store; held outcomes are
    # not attested, and nothing reads attestations for metrics, so the gap stays open.
    for call in ('attest_outcome_corporate_actions(\n            outcome_identity, outcome,',
                 'retain_outcome_action_attestation(\n            attestation_directory,',
                 'load_retained_outcome_attestations(attestation_directory)'):
        assert call in outcome_producer, call
    assert "'attestation requires action selections'" in outcome_producer
    assert "'held_outcome_attestation': 'NOT_PERFORMED'" in outcome_producer
    # decision-outcome-production-v4: held outcomes are re-checked and attested
    # only with the opt-in attest_held_outcomes (requires an attestation
    # directory). decision-outcome-job-v5 passes the flag through only with its
    # opt-in --attest-held-outcomes (refused without an attestation directory)
    # and nothing reads attestations for metrics, so the gap stays open.
    assert 'attest_held_outcomes=False' in outcome_producer
    assert "'held-outcome attestation requires an attestation directory'" in outcome_producer
    # Signature default, the producer call and the CLI call only.
    assert len(re.findall(r'\battest_held_outcomes=', outcome_job)) == 3
    assert 'attest_held_outcomes=False' in outcome_job
    assert 'attest_held_outcomes=attest_held_outcomes)' in outcome_job
    assert "'held-outcome re-check requires an attestation directory'" in outcome_job
    assert "HELD_PATH_THROUGH_RULE = 'HELD_PATH_COMPLETION_DATE_CAIRO_V1'" in outcome_job
    # decision-outcome-job-v4: the job only passes an opt-in attestation
    # directory through (refused without the check); it builds, retains, loads
    # and verifies no attestation itself.
    # Signature default, the producer call and the CLI call only.
    assert len(re.findall(r'\battestation_directory=', outcome_job)) == 3
    assert 'attestation_directory=None' in outcome_job
    assert "'attestation directory requires check_corporate_actions'" in outcome_job
    assert not re.search(r'\b(attest_outcome_corporate_actions|retain_outcome_action_attestation|'
                         r'load_retained_outcome_attestations|verify_outcome_action_attestation|'
                         r'partition_egx_attested_outcomes)\b', outcome_job)
    # decision-outcome-job-v3: only with the opt-in flag, the job requests the
    # by-request selections at the single job clock and passes them unchanged to
    # the producer; it never calls the check or the all-or-nothing v1 loader.
    assert "CONTRACT_VERSION = 'decision-outcome-job-v5'" in outcome_job
    assert 'actions, action_summary = None, None\n    if check_corporate_actions:' in outcome_job
    assert ('load_egx_outcome_action_evidence_by_request(database, raw_root, requests, '
            'as_of=now)') in outcome_job
    assert 'load_egx_outcome_action_evidence(' not in outcome_job
    assert 'check_egx_outcome_corporate_actions' not in outcome_job
    assert len(re.findall(r'\bactions=', outcome_job)) == 1
    assert "'performance_claim': 'NONE'" in outcome_producer
    assert "'promotion': 'NOT_AUTHORIZED'" in outcome_producer
    assert "'live_money': False" in outcome_producer
    # The daily-evidence loader's only caller is the outcome job (a component).
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
