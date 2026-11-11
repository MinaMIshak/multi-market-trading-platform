from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.news_event import (EVENT_CATEGORIES, EventClassification, InstrumentAssociation, NewsEvent,
                            NewsEventSource, select_point_in_time)


AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
CAIRO = timezone(timedelta(hours=2))


def source(**overrides):
    values = dict(source_id='fixture_news', source_event_id='article-1', revision=0,
                  artifact_sha256='a' * 64, published_at=AT - timedelta(hours=2),
                  observed_at=AT - timedelta(hours=1), event_time_status='UNKNOWN')
    values.update(overrides)
    return NewsEventSource(**values)


def matched(market='EGX', **overrides):
    values = dict(status='MATCHED', market=market, basis='EXACT_SOURCE_IDENTIFIER', confidence='EXACT',
                  symbol='TEST', instrument_id=UUID(int=1), mapping_evidence_ids=('fixture-map',))
    values.update(overrides)
    return InstrumentAssociation(**values)


def unclassified():
    return EventClassification(category='UNKNOWN', basis='UNCLASSIFIED')


def event(market='EGX', **source_overrides):
    return NewsEvent(market=market, source=source(**source_overrides), association=matched(market),
                     classification=unclassified())


def test_deterministic_identity_dedup_and_market_separation():
    first, again = event(), event()
    assert first.identity == again.identity and first.dedup_key == again.dedup_key
    assert first == NewsEvent.model_validate_json(first.model_dump_json())
    revised = event(revision=1, artifact_sha256='b' * 64)
    assert revised.dedup_key == first.dedup_key and revised.identity != first.identity
    us = event('US')
    assert us.dedup_key != first.dedup_key and us.identity != first.identity
    assert event(source_event_id='article-2').dedup_key != first.dedup_key


def test_immutable_safety_constants_and_no_trading_fields():
    e = event()
    for obj, key, value in ((e, 'live_money', True), (e, 'trading_effect', 'BUY'),
                            (e.source, 'published_at', AT), (e.association, 'instrument_id', None)):
        with pytest.raises(ValidationError):
            setattr(obj, key, value)
    schema = NewsEvent.model_json_schema()['properties']
    assert schema['live_money']['const'] is False
    assert schema['execution_allowed']['const'] is False
    assert schema['trading_effect']['const'] == 'NONE'
    assert schema['risk_interpretation']['const'] == 'NOT_EVALUATED'
    payload = e.model_dump(mode='python')
    for key, value in (('signal', 'BUY'), ('live_money', True), ('execution_allowed', True),
                       ('trading_effect', 'SELL'), ('mode', 'LIVE'), ('contract_version', 'news-event-v2')):
        with pytest.raises(ValidationError):
            NewsEvent.model_validate(payload | {key: value})


@pytest.mark.parametrize('field', ['published_at', 'observed_at'])
def test_naive_source_timestamps_rejected(field):
    with pytest.raises(ValidationError):
        source(**{field: AT.replace(tzinfo=None) - timedelta(hours=3)})


def test_naive_event_time_and_decision_time_rejected():
    with pytest.raises(ValidationError):
        source(event_time_status='REPORTED', event_at=AT.replace(tzinfo=None))
    with pytest.raises(ValueError):
        select_point_in_time([event()], market='EGX', decision_time=AT.replace(tzinfo=None))


def test_observation_cannot_precede_publication_and_event_time_is_explicit():
    with pytest.raises(ValidationError):
        source(published_at=AT, observed_at=AT - timedelta(seconds=1))
    assert source(published_at=AT, observed_at=AT).observed_at == AT
    with pytest.raises(ValidationError):
        source(event_time_status='REPORTED')
    with pytest.raises(ValidationError):
        source(event_at=AT - timedelta(days=1))
    reported = source(event_time_status='REPORTED', event_at=(AT - timedelta(days=1)).astimezone(CAIRO))
    assert reported.event_at.utcoffset() == timedelta(hours=2)


@pytest.mark.parametrize('overrides', [
    dict(instrument_id=None), dict(symbol=None), dict(mapping_evidence_ids=()),
    dict(basis='NONE'), dict(confidence='NONE'), dict(candidate_instrument_ids=(UUID(int=2),)),
    dict(mapping_evidence_ids=(' ',)), dict(basis='FUZZY_NAME'), dict(confidence='HIGH'),
    dict(market='GLOBAL'),
])
def test_matched_association_requires_exact_inspectable_identity(overrides):
    with pytest.raises(ValidationError):
        matched(**overrides)


def test_ambiguous_and_unresolved_never_name_an_instrument():
    two = (UUID(int=1), UUID(int=2))
    ambiguous = InstrumentAssociation(status='AMBIGUOUS', market='EGX', basis='EXACT_SOURCE_IDENTIFIER',
                                      confidence='NONE', candidate_instrument_ids=two,
                                      mapping_evidence_ids=('fixture-map',))
    assert ambiguous.instrument_id is None and ambiguous.candidate_instrument_ids == two
    for bad in (dict(candidate_instrument_ids=two[:1]), dict(candidate_instrument_ids=(two[0], two[0])),
                dict(instrument_id=two[0]), dict(symbol='TEST'), dict(confidence='EXACT'),
                dict(mapping_evidence_ids=())):
        with pytest.raises(ValidationError):
            InstrumentAssociation(**(ambiguous.model_dump(mode='python') | bad))
    for status in ('UNRESOLVED', 'IDENTITY_UNAVAILABLE'):
        plain = InstrumentAssociation(status=status, market='US', basis='NONE', confidence='NONE')
        assert plain.instrument_id is None and plain.symbol is None
        for bad in (dict(symbol='TEST'), dict(instrument_id=UUID(int=1)),
                    dict(candidate_instrument_ids=two), dict(basis='EXACT_SOURCE_IDENTIFIER')):
            with pytest.raises(ValidationError):
                InstrumentAssociation(**(plain.model_dump(mode='python') | bad))


def test_association_market_must_match_event_market():
    with pytest.raises(ValidationError):
        NewsEvent(market='EGX', source=source(), association=matched('US'), classification=unclassified())
    forged = event().model_copy(update={'association': matched('US')})
    with pytest.raises(ValueError):
        select_point_in_time([forged], market='EGX', decision_time=AT)


def test_classification_is_small_and_labelled_as_derived():
    assert len(EVENT_CATEGORIES) == 15 and 'UNKNOWN' in EVENT_CATEGORIES
    assert EventClassification(category='DIVIDEND', basis='SOURCE_LABEL', classifier_version='src-1')
    for bad in (dict(category='UNKNOWN', basis='RULE', classifier_version='r1'),
                dict(category='DIVIDEND', basis='UNCLASSIFIED'),
                dict(category='DIVIDEND', basis='RULE'),
                dict(category='UNKNOWN', basis='UNCLASSIFIED', classifier_version='r1'),
                dict(category='SENTIMENT_BULLISH', basis='RULE', classifier_version='r1')):
        with pytest.raises(ValidationError):
            EventClassification(**bad)


def test_future_observations_never_leak_into_earlier_decisions():
    past = event(source_event_id='past')
    at_cutoff = event(source_event_id='edge', published_at=AT - timedelta(minutes=5), observed_at=AT)
    published_before_observed_after = event(source_event_id='late', published_at=AT - timedelta(hours=3),
                                            observed_at=AT + timedelta(seconds=1))
    future = event(source_event_id='future', published_at=AT + timedelta(hours=1),
                   observed_at=AT + timedelta(hours=1))
    chosen = select_point_in_time([future, published_before_observed_after, at_cutoff, past],
                                  market='EGX', decision_time=AT)
    assert [e.source.source_event_id for e in chosen] == ['past', 'edge']
    later = select_point_in_time([future, published_before_observed_after, at_cutoff, past],
                                 market='EGX', decision_time=AT + timedelta(hours=2))
    assert [e.source.source_event_id for e in later] == ['late', 'past', 'edge', 'future']


def test_later_revision_does_not_replace_history_but_is_used_once_observed():
    original = event()
    revision = event(revision=1, artifact_sha256='b' * 64, observed_at=AT + timedelta(hours=1))
    assert select_point_in_time([revision, original], market='EGX', decision_time=AT) == (original,)
    assert select_point_in_time([original, revision], market='EGX',
                                decision_time=AT + timedelta(hours=1)) == (revision,)


def test_duplicates_collapse_and_available_conflicts_fail_closed_in_any_order():
    original, duplicate = event(), event()
    assert select_point_in_time([original, duplicate], market='EGX', decision_time=AT) == (original,)
    conflict = event(artifact_sha256='c' * 64)
    newer = event(revision=1, artifact_sha256='b' * 64)
    for ordering in ([original, conflict, newer], [newer, original, conflict], [conflict, newer, original]):
        with pytest.raises(ValueError, match='conflicting'):
            select_point_in_time(ordering, market='EGX', decision_time=AT)
    future_conflict = event(artifact_sha256='c' * 64, observed_at=AT + timedelta(hours=1))
    assert select_point_in_time([original, future_conflict], market='EGX', decision_time=AT) == (original,)


def test_deterministic_ordering_across_offsets_and_input_order():
    a = event(source_event_id='a', published_at=(AT - timedelta(hours=3)).astimezone(CAIRO))
    b = event(source_event_id='b', published_at=AT - timedelta(hours=4))
    c = event(source_id='other_news', source_event_id='a', published_at=AT - timedelta(hours=3))
    expected = (b, a, c)  # a and c share a UTC instant; source_id breaks the tie
    for ordering in ([a, b, c], [c, a, b], [b, c, a]):
        assert select_point_in_time(ordering, market='EGX', decision_time=AT) == expected


def test_markets_are_never_mixed_and_inputs_are_revalidated():
    with pytest.raises(ValueError):
        select_point_in_time([event(), event('US')], market='EGX', decision_time=AT)
    with pytest.raises(ValueError):
        select_point_in_time([event()], market='ALL', decision_time=AT)
    with pytest.raises(ValueError):
        select_point_in_time([event().model_dump()], market='EGX', decision_time=AT)
    for update in ({'live_money': True}, {'trading_effect': 'BUY'}):
        with pytest.raises(ValueError):
            select_point_in_time([event().model_copy(update=update)], market='EGX', decision_time=AT)
    assert select_point_in_time([], market='US', decision_time=AT) == ()
