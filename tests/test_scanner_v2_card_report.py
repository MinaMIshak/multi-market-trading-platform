"""Artificial retained evidence only; no admission, market coverage or edge evidence."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from app.scanner_v2_card_report import (
    CARD_STATES, LIMITATIONS, SCHEMA_VERSION, project_egx_card_report)
from app.scanner_v2_retained_binding import compose_egx_rank_binding
from tests.test_scanner_v2_egx_rank import GENERATED, NEXT_CLOSE, report
from tests.test_scanner_v2_retained_binding import OPEN, RECEIPT, calendar, identity

UTC = timezone.utc
VIEW = datetime.fromisoformat(GENERATED)


def evidence():
    return compose_egx_rank_binding(identity=identity(), calendar=calendar())


def project(observed_at=VIEW, artifact=None, ev=None, expected=None):
    artifact = artifact if artifact is not None else report()
    return project_egx_card_report(
        artifact, expected_sha256=expected or sha256(artifact).hexdigest(),
        evidence=ev or evidence(), observed_at=observed_at)


def test_report_projects_coverage_cards_and_provenance():
    out = project()
    assert out['schema_version'] == SCHEMA_VERSION and out['market'] == 'EGX'
    assert out['feature_status'] == 'OFFLINE_PROJECTION_NOT_ACTIVATED'
    cov = out['coverage']
    assert cov['requested'] == 5 and cov['candidate_count'] == 2 and cov['excluded'] == 3
    assert cov['authoritative_universe_size'] is None
    assert cov['scope_kind'] == 'PUBLISHED_RANKING_SCOPE_NOT_AUTHORITATIVE_UNIVERSE'
    assert [r['reason'] for r in cov['symbols']] == [
        None, None, 'SETUP_NOT_CONFIRMED', 'NO_TRADE', 'IDENTITY_UNBOUND']
    assert cov['identity_exclusions'] == [{'symbol': 'EEEE', 'reason': 'IDENTITY_MISSING'}]
    assert [c['observed_fact']['symbol'] for c in out['cards']] == ['AAAA', 'BBBB']
    ids = [r['decision_id'] for r in cov['symbols'] if r['reason'] is None]
    assert [c['decision_id'] for c in out['cards']] == ids
    prov = out['provenance']
    assert prov['artifact_sha256'] == sha256(report()).hexdigest()
    assert prov['identity_admission'] == 'INTERNAL_SNAPSHOT_VERIFIED'
    assert prov['calendar_admission'] == 'NOT_ESTABLISHED'
    for card in out['cards']:
        refs = card['observed_fact']['provenance']['evidence_ids']
        assert f'egx-ranking-sha256:{prov["artifact_sha256"]}' in refs
        assert f'egid-admission-receipt-sha256:{RECEIPT}' in refs
        assert calendar().evidence_id in refs


def test_report_never_claims_admission_risk_lifecycle_or_execution():
    out = project()
    assert out['live_money'] is out['execution_allowed'] is out['runtime_activation'] is False
    assert out['mode'] == 'SHADOW' and out['promotion'] == 'NOT_AUTHORIZED'
    assert out['risk_status'] == 'NOT_EVALUATED' and out['research_status'] == 'UNVALIDATED'
    assert out['lifecycle_evidence'] == {'status': 'NOT_VERIFIED', 'sample_size': None,
                                         'net_result': None}
    assert out['limitations'] == list(LIMITATIONS)
    for card in out['cards']:
        assert card['live_money'] is card['execution_allowed'] is False
        assert card['risk_decision']['status'] == 'NOT_EVALUATED'
        assert card['risk_decision']['event_risk'] == 'UNKNOWN'
        assert card['research_evidence']['confidence'] == 'UNVALIDATED'
        assert card['promotion'] == 'NOT_AUTHORIZED'
    text = json.dumps(out)
    for forbidden in ('"BUY"', '"SELL"', 'FILLED', 'expected_return'):
        assert forbidden not in text


def test_report_is_deterministic_and_json_serializable():
    a, b = project(), project()
    assert a == b
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@pytest.mark.parametrize('observed_at,state', [
    (VIEW, 'RESEARCH_CANDIDATE'),
    (OPEN - timedelta(microseconds=1), 'RESEARCH_CANDIDATE'),
    (OPEN, 'DATA_STALE'),
    (NEXT_CLOSE, 'PLAN_EXPIRED'),
])
def test_card_states_follow_retained_freshness_without_mutating_plan(observed_at, state):
    out = project(observed_at=observed_at)
    assert out['card_state_counts'] == {s: (2 if s == state else 0) for s in CARD_STATES}
    fresh = project()
    # Plan terms and decision identity are immutable across viewing times.
    assert [c['strategy_inference']['plan'] for c in out['cards']] == \
        [c['strategy_inference']['plan'] for c in fresh['cards']]
    assert [c['decision_id'] for c in out['cards']] == [c['decision_id'] for c in fresh['cards']]


def test_observation_before_ranking_generation_rejected():
    with pytest.raises(ValueError, match='precedes ranking generation'):
        project(observed_at=VIEW - timedelta(microseconds=1))


def test_naive_observation_time_rejected():
    with pytest.raises(ValueError):
        project(observed_at=VIEW.replace(tzinfo=None))


def test_hash_mismatch_and_wrong_evidence_type_rejected():
    with pytest.raises(ValueError, match='hash mismatch'):
        project(expected='0' * 64)
    with pytest.raises(ValueError, match='RetainedEgxRankBinding'):
        project_egx_card_report(report(), expected_sha256=sha256(report()).hexdigest(),
                                evidence=evidence().binding, observed_at=VIEW)


def test_forged_retained_evidence_copy_rejected():
    forged = evidence().model_copy(update={'calendar_admission': 'ADMITTED'})
    with pytest.raises(ValueError):
        project(ev=forged)


def test_report_with_no_confirmed_setups_has_no_cards():
    from tests.test_scanner_v2_egx_rank import record
    artifact = report([record('AAAA', 'WATCHLIST'), record('BBBB', 'NO_TRADE')])
    out = project(artifact=artifact)
    assert out['cards'] == [] and out['coverage']['candidate_count'] == 0
    assert out['card_state_counts'] == {s: 0 for s in CARD_STATES}
    assert out['coverage']['excluded'] == 2
