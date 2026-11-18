"""decision-outcome-corporate-actions-v1: retained EGX action evidence over an outcome path."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
import json
from uuid import UUID

import pytest

from app.data.reference import ActionEvidence, ActionEvidenceRow
from app.decision_observation import canonical_bytes, decision_observation
from app.decision_outcome import TOUCH
from app.decision_outcome_corporate_actions import (
    CONTRACT_VERSION, STATUSES, WINDOW_RULE, check_egx_outcome_corporate_actions)
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision

UTC = timezone.utc
AT = datetime(2026, 11, 5, 6, tzinfo=UTC)                 # Thursday 08:00 Cairo, pre-open
VALID_UNTIL = datetime(2026, 11, 9, 12, 30, tzinfo=UTC)
AS_OF = datetime(2026, 11, 9, 15, tzinfo=UTC)             # after the Monday path completes
PATH = date(2026, 11, 9)
INSTRUMENT = UUID(int=7)
SOURCE = str(UUID(int=101))
REVIEWS = (str(UUID(int=202)),)


def observation(at=AT, market='EGX'):
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version='1',
                          config_id='fixture', symbol='TEST', decision_time=at,
                          data_cutoff=at - timedelta(minutes=1), state='READY',
                          entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    source = DecisionSource(market=market, symbol='TEST', instrument_id=INSTRUMENT,
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


def action(day, kind='DIVIDEND', event=None, **extra):
    return ActionEvidenceRow(event_id=event or f'ev-{day}', effective_date=date(2026, 11, day),
                             action_type=kind, details='fixture only', **extra)


def evidence(actions=(), start=date(2026, 11, 1), end=date(2026, 11, 30),
             published=datetime(2026, 11, 9, 14, tzinfo=UTC), instrument=INSTRUMENT):
    return ActionEvidence(contract='egx-actions-v1', instrument_id=instrument,
                          coverage_start=start, coverage_end=end, published_at=published,
                          complete=True, actions=tuple(actions))


def check(doc=None, obs=None, *, source=SOURCE, reviews=REVIEWS, path=PATH, as_of=AS_OF):
    identity, record = obs or observation()
    return check_egx_outcome_corporate_actions(
        identity, record, evidence() if doc is None else doc, source_ingestion_id=source,
        review_ingestion_ids=reviews, path_through=path, as_of=as_of)


def test_clear_only_inside_complete_declared_coverage():
    identity, record = observation()
    # Actions before the data_as_of date and after the path are outside the window.
    doc = evidence((action(3), action(12, 'SPLIT', new_shares=D('2'), old_shares=D('1'))))
    report = check(doc, (identity, record))
    assert report['status'] == 'CLEAR' and report['actions_in_path'] == []
    assert report['clear_basis'] == 'COMPLETE_DECLARED_COVERAGE'
    assert report['window'] == {'rule': WINDOW_RULE, 'first_date': '2026-11-05',
                                'last_date': '2026-11-09', 'timezone': 'Africa/Cairo'}
    assert report['evidence'] == {
        'contract': 'egx-actions-v1', 'coverage_start': '2026-11-01',
        'coverage_end': '2026-11-30', 'published_at': '2026-11-09T14:00:00Z',
        'complete': True, 'actions_declared': 2, 'source_ingestion_id': SOURCE,
        'review_ingestion_ids': list(REVIEWS), 'review_verification': 'CALLER_ASSERTED_UNVERIFIED'}
    assert report['observation_identity'] == identity
    assert report['decision_id'] == record['decision_id']
    assert (report['market'], report['symbol'], report['instrument_id']) == (
        'EGX', 'TEST', str(INSTRUMENT))
    assert report['as_of'] == '2026-11-09T15:00:00Z'


def test_every_action_in_the_window_is_listed_in_order_whatever_its_type():
    doc = evidence((action(9, 'SPLIT', 'ev-b', new_shares=D('2'), old_shares=D('1')),
                    action(5, 'DIVIDEND', 'ev-a'),       # on the data_as_of date: included
                    action(8, 'OTHER', 'ev-c', adjustment_effect='NONE'),
                    action(2), action(20, 'DELISTING')))
    report = check(doc)
    assert report['status'] == 'ACTIONS_IN_PATH' and report['clear_basis'] is None
    assert report['actions_in_path'] == [
        {'event_id': 'ev-a', 'effective_date': '2026-11-05', 'action_type': 'DIVIDEND',
         'adjustment_effect': None},
        {'event_id': 'ev-c', 'effective_date': '2026-11-08', 'action_type': 'OTHER',
         'adjustment_effect': 'NONE'},
        {'event_id': 'ev-b', 'effective_date': '2026-11-09', 'action_type': 'SPLIT',
         'adjustment_effect': None}]
    assert report['status'] in STATUSES
    # Path end is inclusive: an action on the final path date is in the path.
    assert check(evidence((action(9),)))['status'] == 'ACTIONS_IN_PATH'
    assert check(evidence((action(10),)))['status'] == 'CLEAR'


@pytest.mark.parametrize('start,end', [
    (date(2026, 11, 6), date(2026, 11, 30)),   # starts after the data_as_of date
    (date(2026, 11, 1), date(2026, 11, 8)),    # ends before the path date
    (date(2026, 11, 6), date(2026, 11, 8)),
])
def test_coverage_that_does_not_span_the_window_fails_closed(start, end):
    with pytest.raises(ValueError, match='coverage does not span'):
        check(evidence(start=start, end=end))
    # Exact coverage of the window is enough.
    assert check(evidence(start=date(2026, 11, 5), end=PATH))['status'] == 'CLEAR'


def test_point_in_time_and_identity_fail_closed():
    with pytest.raises(ValueError, match='published after as_of'):
        check(evidence(published=AS_OF + timedelta(seconds=1)))
    assert check(evidence(published=AS_OF))['status'] == 'CLEAR'
    with pytest.raises(ValueError, match='instrument mismatch'):
        check(evidence(instrument=UUID(int=8)))
    with pytest.raises(ValueError, match='before detection'):
        check(path=date(2026, 11, 4))
    with pytest.raises(ValueError, match='after as_of'):
        check(path=date(2026, 11, 10))
    with pytest.raises(ValueError, match='timezone-aware as_of'):
        check(as_of=AS_OF.replace(tzinfo=None))
    with pytest.raises(ValueError, match='path_through date required'):
        check(path=datetime(2026, 11, 9, tzinfo=UTC))
    with pytest.raises(ValueError, match='EGX observation required'):
        check(obs=observation(market='US'))


def test_edited_record_and_unchecked_evidence_fail_closed():
    identity, record = observation()
    edited = deepcopy(record)
    edited['data_as_of'] = '2026-11-06T06:00:00Z'      # moving the window start is an edit
    with pytest.raises(ValueError):
        check(obs=(identity, edited))
    with pytest.raises(ValueError):
        check(obs=('0' * 64, record))
    with pytest.raises(ValueError, match='ActionEvidence required'):
        check(json.loads(evidence().model_dump_json()))
    bypass = ActionEvidence.model_construct(
        contract='egx-actions-v1', instrument_id=INSTRUMENT, coverage_start=date(2026, 11, 30),
        coverage_end=date(2026, 11, 1), published_at=AS_OF, complete=True, actions=())
    with pytest.raises(ValueError):
        check(bypass)


HEX_SOURCE = str(UUID(int=0xabc))             # contains hex letters, so case matters


@pytest.mark.parametrize('source,reviews', [
    (HEX_SOURCE.upper(), REVIEWS),             # not canonical: upper-case hex
    ('{' + HEX_SOURCE + '}', REVIEWS),         # not canonical: braces
    (HEX_SOURCE.replace('-', ''), REVIEWS),    # not canonical: no hyphens
    ('urn:uuid:' + HEX_SOURCE, REVIEWS),       # not canonical: URN form
    (SOURCE, (HEX_SOURCE.upper(),)),           # review id not canonical
    ('not-a-uuid', REVIEWS),
    (SOURCE, ()),                              # a review is required
    (SOURCE, list(REVIEWS)),                   # tuple required
    (SOURCE, REVIEWS + REVIEWS),               # duplicate
    (SOURCE, (SOURCE,)),                       # a document cannot review itself
    (SOURCE, (UUID(int=202),)),                # strings only
])
def test_provenance_ids_fail_closed(source, reviews):
    with pytest.raises(ValueError):
        check(source=source, reviews=reviews)


def test_canonical_hex_provenance_ids_are_accepted():
    # Guard the rejection cases above: the canonical lower-case form with hex letters passes.
    report = check(source=HEX_SOURCE, reviews=REVIEWS)
    assert report['evidence']['source_ingestion_id'] == HEX_SOURCE


def test_check_is_deterministic_labelled_and_changes_nothing():
    identity, record = observation()
    before = deepcopy(record)
    doc = evidence((action(8, 'RIGHTS'),))
    first = check(doc, (identity, record), reviews=(str(UUID(int=303)), str(UUID(int=202))))
    second = check(doc, (identity, record), reviews=(str(UUID(int=202)), str(UUID(int=303))))
    assert first == second and record == before
    assert first['evidence']['review_ingestion_ids'] == [str(UUID(int=202)), str(UUID(int=303))]
    assert first['contract_version'] == CONTRACT_VERSION == 'decision-outcome-corporate-actions-v1'
    # Nothing is adjusted or evaluated; the result claims nothing.
    assert first['price_adjustment'] == 'NOT_APPLIED' and first['outcome'] == 'NOT_EVALUATED'
    assert first['performance_claim'] == 'NONE' and first['mode'] == 'SHADOW'
    assert first['live_money'] is False and first['execution_allowed'] is False
    assert first['promotion'] == 'NOT_AUTHORIZED'
    json.dumps(first, allow_nan=False)
