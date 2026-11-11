from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import UUID

import pytest

from app.news_event import (
    EventClassification, InstrumentAssociation, NewsEvent, NewsEventSource, select_point_in_time)
from app.news_event_record import (
    MAX_BYTES, RetainedNewsEventError, encode_news_event, read_retained_news_event)


AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
CAIRO = timezone(timedelta(hours=2))


def event(eid='e1', market='EGX', published=AT - timedelta(hours=2), observed=None, revision=0,
          reported=None):
    src = NewsEventSource(source_id='fixture_news', source_event_id=eid, revision=revision,
                          artifact_sha256='a' * 64, published_at=published,
                          observed_at=observed or published + timedelta(minutes=5),
                          event_time_status='REPORTED' if reported else 'UNKNOWN', event_at=reported)
    assoc = InstrumentAssociation(status='MATCHED', market=market, basis='EXACT_SOURCE_IDENTIFIER',
                                  confidence='EXACT', symbol='TEST', instrument_id=UUID(int=1),
                                  mapping_evidence_ids=('fixture-map',))
    return NewsEvent(market=market, source=src, association=assoc,
                     classification=EventClassification(category='DIVIDEND', basis='SOURCE_LABEL',
                                                        classifier_version='src-1'))


def canon(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def read(payload):
    return read_retained_news_event(payload, expected_sha256=sha256(payload).hexdigest())


def mutated(mutate):
    document = json.loads(encode_news_event(event()))
    mutate(document)
    return canon(document)


def test_retained_bytes_digest_is_the_record_identity_and_round_trips():
    original = event(published=datetime(2026, 11, 5, 9, tzinfo=CAIRO), reported=AT - timedelta(days=1))
    payload = encode_news_event(original)
    assert sha256(payload).hexdigest() == original.identity
    decoded = read_retained_news_event(payload, expected_sha256=original.identity)
    assert type(decoded) is NewsEvent
    assert decoded.identity == original.identity
    assert decoded.model_dump(mode='json') == original.model_dump(mode='json')
    assert decoded.source.published_at.utcoffset() == timedelta(hours=2)  # offset preserved
    assert encode_news_event(decoded) == payload
    assert decoded.live_money is False and decoded.trading_effect == 'NONE' and decoded.mode == 'SHADOW'


def test_encoding_is_deterministic_and_markets_never_share_bytes():
    assert encode_news_event(event()) == encode_news_event(event())
    assert encode_news_event(event(market='EGX')) != encode_news_event(event(market='US'))


def test_decoded_records_keep_point_in_time_selection_unchanged():
    early = event('a')
    late = event('a', revision=1, observed=AT + timedelta(minutes=1))
    future = event('b', published=AT + timedelta(hours=1))
    records = [early, late, future]
    decoded = [read(encode_news_event(e)) for e in records]
    picked = select_point_in_time(decoded, market='EGX', decision_time=AT)
    assert [e.identity for e in picked] == [early.identity]
    assert [e.identity for e in picked] == [
        e.identity for e in select_point_in_time(records, market='EGX', decision_time=AT)]


def test_hash_mismatch_and_invalid_expected_digest_rejected():
    payload = encode_news_event(event())
    with pytest.raises(RetainedNewsEventError, match='hash mismatch'):
        read_retained_news_event(payload, expected_sha256='0' * 64)
    for bad in (sha256(payload).hexdigest().upper(), 'a' * 63, None, b'a' * 64):
        with pytest.raises(RetainedNewsEventError, match='expected_sha256'):
            read_retained_news_event(payload, expected_sha256=bad)


@pytest.mark.parametrize('payload', ['{}', bytearray(b'{}'), b'', b'x' * (MAX_BYTES + 1), None])
def test_unbounded_or_non_bytes_payload_rejected(payload):
    with pytest.raises(RetainedNewsEventError, match='bounded bytes'):
        read_retained_news_event(payload, expected_sha256='0' * 64)


def test_non_canonical_bytes_rejected_even_with_matching_digest():
    document = json.loads(encode_news_event(event()))
    pretty = json.dumps(document, sort_keys=True, indent=2).encode()
    with pytest.raises(RetainedNewsEventError, match='not canonical'):
        read(pretty)
    with pytest.raises(RetainedNewsEventError, match='not canonical'):
        read(encode_news_event(event()) + b'\n')


def test_duplicate_keys_non_finite_numbers_and_non_objects_rejected():
    payload = encode_news_event(event())
    with pytest.raises(RetainedNewsEventError, match='duplicate JSON key'):
        read(b'{"market":"US",' + payload[1:])
    with pytest.raises(RetainedNewsEventError, match='non-finite'):
        read(b'{"revision":NaN}')
    for bad in (b'[]', b'"x"', b'\xff\xfe', b'{'):
        with pytest.raises(RetainedNewsEventError, match='strict JSON|JSON object'):
            read(bad)


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(signal='BUY'),
    lambda d: d.update(live_money=True),
    lambda d: d.update(execution_allowed=True),
    lambda d: d.update(trading_effect='BUY'),
    lambda d: d.update(mode='LIVE'),
    lambda d: d.update(contract_version='news-event-v2'),
    lambda d: d['association'].update(market='US'),
    lambda d: d['source'].update(published_at=d['source']['published_at'][:19]),
    lambda d: d['source'].update(observed_at=d['source']['observed_at'][:19]),
    lambda d: d['source'].update(revision=0.0),
    lambda d: d['source'].update(observed_at='2026-11-01T00:00:00+00:00'),
])
def test_records_violating_news_event_v1_rejected(mutate):
    with pytest.raises(RetainedNewsEventError, match='validation'):
        read(mutated(mutate))


def test_encode_requires_exact_news_event():
    with pytest.raises(ValueError, match='exact NewsEvent'):
        encode_news_event(event().model_dump(mode='python'))

    class Sub(NewsEvent):
        pass

    with pytest.raises(ValueError, match='exact NewsEvent'):
        encode_news_event(Sub.model_validate(event().model_dump(mode='python')))
