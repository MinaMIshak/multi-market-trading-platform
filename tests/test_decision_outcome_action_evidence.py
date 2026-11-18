"""decision-outcome-action-evidence-v1: retained action selection for outcome-path checks."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
import json
import sqlite3
from uuid import UUID

import pytest

from app.data.raw_store import ImmutableRawStore
from app.data.reference import ActionEvidence
from app.decision_observation import canonical_bytes, decision_observation
from app.decision_outcome import TOUCH
from app.decision_outcome_action_evidence import (
    BY_REQUEST_CONTRACT_VERSION, CONTRACT_VERSION, LOAD_REASONS, REQUEST_STATUSES,
    SELECTION_FIELDS, ActionEvidenceLoadError, OutcomeActionSelection,
    load_egx_outcome_action_evidence, load_egx_outcome_action_evidence_by_request)
from app.decision_outcome_corporate_actions import WINDOW_RULE, check_egx_outcome_corporate_actions
from app.storage.database import Database
from app.storage.reference_repository import ReferenceRepository
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision

UTC = timezone.utc
AT = datetime(2026, 11, 5, 6, tzinfo=UTC)                 # Thursday 08:00 Cairo, pre-open
VALID_UNTIL = datetime(2026, 11, 9, 12, 30, tzinfo=UTC)
PATH = date(2026, 11, 9)
INSTRUMENT = UUID(int=7)
OTHER = UUID(int=8)
PUBLISHED = '2026-11-09T14:00:00+00:00'


def now():
    # Reference ingestion records the real receipt clock, so as_of follows it.
    return datetime.now(UTC) + timedelta(seconds=1)


def observation(at=AT, market='EGX', instrument=INSTRUMENT):
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version='1',
                          config_id='fixture', symbol='TEST', decision_time=at,
                          data_cutoff=at - timedelta(minutes=1), state='READY',
                          entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    source = DecisionSource(market=market, symbol='TEST', instrument_id=instrument,
                            source_id='fixture', artifact_sha256='a' * 64,
                            evidence_ids=('fixture-only',), data_as_of=candidate.data_cutoff,
                            available_at=at, fresh_until=at + timedelta(hours=1),
                            completed_bar=True)
    terms = DecisionTerms(entry_trigger=TOUCH, entry_low=D('9.5'), entry_high=D('10.5'),
                          entry_reference=D('10'), stop=D('9'), target=D('12'),
                          valid_until=VALID_UNTIL, max_holding_sessions=3, flat_by=None)
    record = decision_observation(TradeDecision(candidate=candidate, source=source, terms=terms),
                                  None, recorded_at=at + timedelta(minutes=5))
    return sha256(canonical_bytes(record)).hexdigest(), record


@pytest.fixture
def repo(tmp_path):
    db = Database(tmp_path / 'isolated.db')
    db.initialize()
    return ReferenceRepository(db, ImmutableRawStore(tmp_path / 'raw'))


def ingest(repo, doc):
    return repo.ingest(provider='synthetic', payload=json.dumps(doc).encode(),
                       source_uri='fixture://synthetic', contract=doc['contract'])


def review(repo, manifest):
    return ingest(repo, dict(
        contract='egx-source-review-v1', subject_ingestion_id=str(manifest.ingestion_id),
        subject_sha256=manifest.sha256, subject_provider=manifest.provider,
        subject_contract='egx-actions-v1', reviewed_at=datetime.now(UTC).isoformat(),
        reviewer='fixture-reviewer', evidence_uri='fixture://validation',
        methodology='synthetic fixture comparison', approved=True))


def actions_doc(events=(), start='2026-11-01', end='2026-11-30', instrument=INSTRUMENT):
    return dict(contract='egx-actions-v1', instrument_id=str(instrument), coverage_start=start,
                coverage_end=end, published_at=PUBLISHED, complete=True, actions=list(events))


def dividend(day, event=None):
    return dict(event_id=event or f'ev-{day}', effective_date=f'2026-11-{day:02d}',
                action_type='DIVIDEND', details='fixture only')


def reviewed(repo, doc):
    manifest = ingest(repo, doc)
    return manifest, review(repo, manifest)


def load(repo, requests, *, as_of=None, raw_root=None):
    return load_egx_outcome_action_evidence(
        repo.database, str(repo.raw_store.root) if raw_root is None else raw_root, requests,
        as_of=now() if as_of is None else as_of)


def issue_count(repo):
    with repo.database.connect() as con:
        return con.execute('SELECT COUNT(*) FROM data_quality_issues').fetchone()[0]


def test_selection_is_the_repository_choice_and_feeds_the_unchanged_check(repo):
    doc = actions_doc((dividend(3), dividend(8)))
    manifest, review_manifest = reviewed(repo, doc)
    identity, record = observation()
    as_of = now()
    before = deepcopy(record)
    issues = issue_count(repo)
    result = load(repo, ((identity, record, PATH),), as_of=as_of)
    # A successful load writes nothing and leaves the record untouched.
    assert issue_count(repo) == issues and record == before
    selection = result['selections'][identity]
    assert type(selection) is OutcomeActionSelection
    assert selection.evidence == ActionEvidence.model_validate(doc)
    assert selection.source_ingestion_id == str(manifest.ingestion_id)
    assert selection.review_ingestion_ids == (str(review_manifest.ingestion_id),)
    assert result['sources'] == [{
        'observation_identity': identity, 'decision_id': record['decision_id'],
        'symbol': 'TEST', 'instrument_id': str(INSTRUMENT),
        'window': {'rule': WINDOW_RULE, 'first_date': '2026-11-05', 'last_date': '2026-11-09',
                   'timezone': 'Africa/Cairo'},
        'source_ingestion_id': str(manifest.ingestion_id), 'raw_path': manifest.raw_path,
        'raw_sha256': manifest.sha256, 'provider': 'synthetic',
        'received_at': result['sources'][0]['received_at'],
        'completed_at': result['sources'][0]['completed_at'],
        'coverage_start': '2026-11-01', 'coverage_end': '2026-11-30',
        'published_at': '2026-11-09T14:00:00Z', 'actions_declared': 2,
        'review_ingestion_ids': [str(review_manifest.ingestion_id)]}]
    assert datetime.fromisoformat(result['sources'][0]['received_at']) <= as_of
    # The loader performs no check; the unchanged check consumes the selection as is.
    report = check_egx_outcome_corporate_actions(
        identity, record, selection.evidence,
        source_ingestion_id=selection.source_ingestion_id,
        review_ingestion_ids=selection.review_ingestion_ids, path_through=PATH, as_of=as_of)
    assert report['status'] == 'ACTIONS_IN_PATH'
    assert [a['event_id'] for a in report['actions_in_path']] == ['ev-8']
    assert report['window'] == result['sources'][0]['window']


def test_result_is_labelled_and_claims_nothing(repo):
    reviewed(repo, actions_doc())
    identity, record = observation()
    as_of = now()
    result = load(repo, ((identity, record, PATH),), as_of=as_of)
    assert result['schema_version'] == CONTRACT_VERSION == 'decision-outcome-action-evidence-v1'
    assert result['market'] == 'EGX'
    assert result['as_of'] == as_of.isoformat().replace('+00:00', 'Z')
    assert result['request_selection'] == 'CALLER_NAMED_OBSERVATIONS'
    assert result['evidence_selection'] == 'REFERENCE_REPOSITORY_ACTIONS_POINT_IN_TIME'
    assert result['review_verification'] == 'REFERENCE_REPOSITORY_REQUIRE_REVIEW'
    assert result['reviewer_authority'] == 'NOT_ATTESTED'
    assert result['corporate_action_check'] == 'NOT_PERFORMED_BY_LOADER'
    assert result['price_adjustment'] == 'NOT_APPLIED' and result['outcome'] == 'NOT_EVALUATED'
    assert result['database_writes'] == 'NONE_ON_SUCCESS'
    assert result['performance_claim'] == 'NONE' and result['mode'] == 'SHADOW'
    assert result['live_money'] is False and result['execution_allowed'] is False
    assert result['promotion'] == 'NOT_AUTHORIZED'
    assert 'status' not in result and 'clear_basis' not in json.dumps(result['sources'])


def test_requests_keep_caller_order_with_one_window_each(repo):
    reviewed(repo, actions_doc())
    reviewed(repo, actions_doc(instrument=OTHER))
    first = observation()
    second = observation(at=datetime(2026, 11, 8, 6, tzinfo=UTC), instrument=OTHER)
    third = observation(at=datetime(2026, 11, 2, 6, tzinfo=UTC))
    result = load(repo, ((second[0], second[1], date(2026, 11, 10)), (first[0], first[1], PATH),
                         (third[0], third[1], date(2026, 11, 3))))
    assert [s['observation_identity'] for s in result['sources']] == [
        second[0], first[0], third[0]]
    assert [(s['window']['first_date'], s['window']['last_date']) for s in result['sources']] == [
        ('2026-11-08', '2026-11-10'), ('2026-11-05', '2026-11-09'), ('2026-11-02', '2026-11-03')]
    assert result['selections'][second[0]].evidence.instrument_id == OTHER
    assert set(result['selections']) == {first[0], second[0], third[0]}


@pytest.mark.parametrize('docs', [
    (),                                                        # nothing retained
    (actions_doc(start='2026-11-06'),),                        # starts after data_as_of date
    (actions_doc(end='2026-11-08'),),                          # ends before the path date
    (actions_doc(), actions_doc((dividend(9),))),              # two overlapping documents
    (actions_doc(instrument=OTHER),),                          # another instrument only
])
def test_missing_partial_or_ambiguous_coverage_is_refused(repo, docs):
    for doc in docs:
        reviewed(repo, doc)
    identity, record = observation()
    issues = issue_count(repo)
    with pytest.raises(ActionEvidenceLoadError) as error:
        load(repo, ((identity, record, PATH),))
    assert error.value.reason == 'ACTIONS_UNAVAILABLE' and error.value.reason in LOAD_REASONS
    # The unchanged repository records its existing audit row on refusal (labelled).
    assert issue_count(repo) == issues + 1


def test_unreviewed_evidence_is_refused(repo):
    ingest(repo, actions_doc())
    identity, record = observation()
    with pytest.raises(ActionEvidenceLoadError) as error:
        load(repo, ((identity, record, PATH),))
    assert error.value.reason == 'ACTIONS_UNAVAILABLE'
    assert 'source validation evidence unavailable' in str(error.value)


def test_evidence_received_after_as_of_is_invisible(repo):
    reviewed(repo, actions_doc())
    identity, record = observation()
    # Retained now (after November 10), so a November 10 cut cannot see it.
    with pytest.raises(ActionEvidenceLoadError) as error:
        load(repo, ((identity, record, PATH),), as_of=datetime(2026, 11, 10, tzinfo=UTC))
    assert error.value.reason == 'ACTIONS_UNAVAILABLE'


def test_tampered_retained_bytes_are_refused(repo):
    manifest, _ = reviewed(repo, actions_doc())
    target = repo.raw_store.root / manifest.raw_path
    target.chmod(0o644)
    target.write_bytes(json.dumps(actions_doc((dividend(8),))).encode())
    identity, record = observation()
    with pytest.raises(ActionEvidenceLoadError) as error:
        load(repo, ((identity, record, PATH),))
    assert error.value.reason == 'ACTIONS_UNAVAILABLE'


def test_bad_requests_fail_closed_before_any_database_read(repo, tmp_path):
    identity, record = observation()
    edited = deepcopy(record)
    edited['data_as_of'] = '2026-11-01T06:00:00Z'       # widening the window is an edit
    us = observation(market='US')
    cases = [
        dict(requests=()),
        dict(requests=[(identity, record, PATH)]),
        dict(requests=((identity, record),)),
        dict(requests=((identity, record, PATH), (identity, record, PATH))),
        dict(requests=((identity, edited, PATH),)),
        dict(requests=(('0' * 64, record, PATH),)),
        dict(requests=((us[0], us[1], PATH),)),
        dict(requests=((identity, record, date(2026, 11, 4)),)),          # before detection
        dict(requests=((identity, record, datetime(2026, 11, 9, tzinfo=UTC)),)),
        dict(requests=((identity, record, PATH),), as_of=datetime(2026, 11, 8, tzinfo=UTC)),
        dict(requests=((identity, record, PATH),), as_of=datetime(2026, 11, 20)),  # naive
        dict(requests=((identity, record, PATH),), raw_root='relative/raw'),
    ]
    for case in cases:
        with pytest.raises(ActionEvidenceLoadError) as error:
            load(repo, case['requests'], as_of=case.get('as_of'), raw_root=case.get('raw_root'))
        assert error.value.reason == 'CONFIG_INVALID', case
    missing = tmp_path / 'absent' / 'missing.db'
    with pytest.raises(ActionEvidenceLoadError) as error:
        load_egx_outcome_action_evidence(Database(missing), str(tmp_path), ((identity, record, PATH),),
                                         as_of=now())
    assert error.value.reason == 'DATABASE_UNAVAILABLE'
    assert not missing.exists() and not missing.parent.exists()


def test_load_is_deterministic(repo):
    reviewed(repo, actions_doc((dividend(6),)))
    identity, record = observation()
    as_of = now()
    first = load(repo, ((identity, record, PATH),), as_of=as_of)
    second = load(repo, ((identity, record, PATH),), as_of=as_of)
    assert first == second
    json.dumps({k: v for k, v in first.items() if k != 'selections'}, allow_nan=False)


# decision-outcome-action-evidence-by-request-v1: one observation's missing or
# rejected evidence no longer blocks the others; it only leaves that observation
# without a selection (the producer gate then reports AWAITING_ACTION_EVIDENCE).

def load_by_request(repo, requests, *, as_of=None, raw_root=None):
    return load_egx_outcome_action_evidence_by_request(
        repo.database, str(repo.raw_store.root) if raw_root is None else raw_root, requests,
        as_of=now() if as_of is None else as_of)


def window(first, last):
    return {'rule': WINDOW_RULE, 'first_date': first, 'last_date': last,
            'timezone': 'Africa/Cairo'}


def without_status(sources):
    return [{k: v for k, v in s.items() if k not in ('status', 'detail')} for s in sources]


def test_by_request_equals_the_whole_load_when_every_request_is_selected(repo):
    reviewed(repo, actions_doc((dividend(6),)))
    reviewed(repo, actions_doc(instrument=OTHER))
    first = observation()
    second = observation(at=datetime(2026, 11, 8, 6, tzinfo=UTC), instrument=OTHER)
    requests = ((second[0], second[1], date(2026, 11, 10)), (first[0], first[1], PATH))
    as_of = now()
    issues = issue_count(repo)
    whole = load(repo, requests, as_of=as_of)
    result = load_by_request(repo, requests, as_of=as_of)
    assert issue_count(repo) == issues
    assert result['selections'] == whole['selections']
    assert [s['status'] for s in result['sources']] == ['SELECTED', 'SELECTED']
    assert [s['detail'] for s in result['sources']] == [None, None]
    assert without_status(result['sources']) == whole['sources']
    assert result['request_counts'] == {
        'requests': 2, 'SELECTED': 2, 'ACTIONS_UNAVAILABLE': 0, 'ACTIONS_REJECTED': 0}
    assert result['database_writes'] == 'NONE'


def test_missing_coverage_refuses_only_that_request(repo):
    manifest, review_manifest = reviewed(repo, actions_doc())      # INSTRUMENT only
    first = observation()
    second = observation(at=datetime(2026, 11, 8, 6, tzinfo=UTC), instrument=OTHER)
    requests = ((second[0], second[1], date(2026, 11, 10)), (first[0], first[1], PATH))
    issues = issue_count(repo)
    result = load_by_request(repo, requests)
    # The unchanged repository records its existing audit row for the refusal.
    assert issue_count(repo) == issues + 1
    assert set(result['selections']) == {first[0]}
    refused, selected = result['sources']
    assert refused == {
        'observation_identity': second[0], 'decision_id': second[1]['decision_id'],
        'symbol': 'TEST', 'instrument_id': str(OTHER),
        'window': window('2026-11-08', '2026-11-10'),
        **dict.fromkeys(SELECTION_FIELDS),
        'status': 'ACTIONS_UNAVAILABLE', 'detail': refused['detail']}
    assert refused['detail'].startswith(f'{second[0]}: ') and 'coverage' in refused['detail']
    assert selected['status'] == 'SELECTED' and selected['detail'] is None
    assert selected['source_ingestion_id'] == str(manifest.ingestion_id)
    assert selected['review_ingestion_ids'] == [str(review_manifest.ingestion_id)]
    assert result['request_counts'] == {
        'requests': 2, 'SELECTED': 1, 'ACTIONS_UNAVAILABLE': 1, 'ACTIONS_REJECTED': 0}
    assert result['database_writes'] == 'REPOSITORY_AUDIT_ROWS_POSSIBLE_FOR_REFUSED_REQUESTS'
    # The v1 whole load still refuses everything for the same requests.
    with pytest.raises(ActionEvidenceLoadError) as error:
        load(repo, requests)
    assert error.value.reason == 'ACTIONS_UNAVAILABLE'


def test_coverage_ending_before_one_path_refuses_only_that_request(repo):
    reviewed(repo, actions_doc(end='2026-11-09'))
    first = observation()
    later = observation(at=datetime(2026, 11, 8, 6, tzinfo=UTC))   # same instrument
    result = load_by_request(repo, ((first[0], first[1], PATH),
                                    (later[0], later[1], date(2026, 11, 10))))
    assert [s['status'] for s in result['sources']] == ['SELECTED', 'ACTIONS_UNAVAILABLE']
    assert set(result['selections']) == {first[0]}
    # Never resolved by stretching or substituting the covered document.
    assert result['sources'][1]['coverage_end'] is None
    assert result['sources'][1]['window'] == window('2026-11-08', '2026-11-10')


def test_rejected_selection_refuses_only_that_request(repo, monkeypatch):
    reviewed(repo, actions_doc())
    original = ReferenceRepository.actions

    def other_instrument(self, *, instrument_id, start, end, as_of):
        # Defensive re-check: a document for a different instrument is rejected.
        return original(self, instrument_id=INSTRUMENT, start=start, end=end, as_of=as_of)

    monkeypatch.setattr(ReferenceRepository, 'actions', other_instrument)
    first = observation()
    second = observation(at=datetime(2026, 11, 8, 6, tzinfo=UTC), instrument=OTHER)
    result = load_by_request(repo, ((first[0], first[1], PATH),
                                    (second[0], second[1], date(2026, 11, 10))))
    assert [s['status'] for s in result['sources']] == ['SELECTED', 'ACTIONS_REJECTED']
    assert 'does not cover the window' in result['sources'][1]['detail']
    assert set(result['selections']) == {first[0]}
    assert result['request_counts']['ACTIONS_REJECTED'] == 1


def test_by_request_still_refuses_bad_requests_and_database_failures(repo, tmp_path, monkeypatch):
    reviewed(repo, actions_doc())
    identity, record = observation()
    other = observation(at=datetime(2026, 11, 8, 6, tzinfo=UTC), instrument=OTHER)
    for requests in (
            ((identity, record, PATH), (identity, record, PATH)),               # duplicate
            ((other[0], other[1], date(2026, 11, 10)),
             (identity, record, date(2026, 11, 4))),                           # before detection
            ((identity, record, PATH), ('0' * 64, record, PATH))):             # wrong address
        issues = issue_count(repo)
        with pytest.raises(ActionEvidenceLoadError) as error:
            load_by_request(repo, requests)
        assert error.value.reason == 'CONFIG_INVALID', requests
        assert issue_count(repo) == issues                                     # nothing read
    missing = tmp_path / 'absent' / 'missing.db'
    with pytest.raises(ActionEvidenceLoadError) as error:
        load_egx_outcome_action_evidence_by_request(
            Database(missing), str(tmp_path), ((identity, record, PATH),), as_of=now())
    assert error.value.reason == 'DATABASE_UNAVAILABLE'
    assert not missing.exists() and not missing.parent.exists()

    def locked(self, **kwargs):
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(ReferenceRepository, 'actions', locked)
    with pytest.raises(ActionEvidenceLoadError) as error:
        load_by_request(repo, ((identity, record, PATH),))
    assert error.value.reason == 'DATABASE_UNAVAILABLE'


def test_by_request_result_is_labelled_deterministic_and_claims_nothing(repo):
    reviewed(repo, actions_doc())
    first = observation()
    second = observation(at=datetime(2026, 11, 8, 6, tzinfo=UTC), instrument=OTHER)
    requests = ((first[0], first[1], PATH), (second[0], second[1], date(2026, 11, 10)))
    as_of = now()
    result = load_by_request(repo, requests, as_of=as_of)
    assert result == load_by_request(repo, requests, as_of=as_of)
    assert result['schema_version'] == BY_REQUEST_CONTRACT_VERSION == (
        'decision-outcome-action-evidence-by-request-v1')
    assert REQUEST_STATUSES == ('SELECTED', 'ACTIONS_UNAVAILABLE', 'ACTIONS_REJECTED')
    assert {s['status'] for s in result['sources']} <= set(REQUEST_STATUSES)
    assert result['market'] == 'EGX'
    assert result['as_of'] == as_of.isoformat().replace('+00:00', 'Z')
    assert result['request_selection'] == 'CALLER_NAMED_OBSERVATIONS'
    assert result['refusal_scope'] == 'PER_REQUEST_EVIDENCE_ONLY'
    assert result['refused_request_effect'] == 'NO_SELECTION_FOR_REQUEST'
    assert result['evidence_selection'] == 'REFERENCE_REPOSITORY_ACTIONS_POINT_IN_TIME'
    assert result['review_verification'] == 'REFERENCE_REPOSITORY_REQUIRE_REVIEW'
    assert result['reviewer_authority'] == 'NOT_ATTESTED'
    assert result['corporate_action_check'] == 'NOT_PERFORMED_BY_LOADER'
    assert result['price_adjustment'] == 'NOT_APPLIED' and result['outcome'] == 'NOT_EVALUATED'
    assert result['performance_claim'] == 'NONE' and result['mode'] == 'SHADOW'
    assert result['live_money'] is False and result['execution_allowed'] is False
    assert result['promotion'] == 'NOT_AUTHORIZED'
    assert 'clear_basis' not in json.dumps(result['sources'])
    json.dumps({k: v for k, v in result.items() if k != 'selections'}, allow_nan=False)
