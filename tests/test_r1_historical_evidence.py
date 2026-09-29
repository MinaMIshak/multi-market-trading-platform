"""Engineering fixtures only: no historical market evidence or acquisition."""
import builtins
import hashlib
import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.research.historical_evidence import (
    HistoricalAttachmentReference as Ref,
    HistoricalAvailability as Availability,
    HistoricalAvailabilityEvidence as Evidence,
    HistoricalEvidenceAttachment as Attachment,
    HistoricalEvidencePackage as Package,
    HistoricalRawReceipt as Receipt,
    HistoricalSourceReview as Review,
    require_historical_evidence,
)

DECISION = datetime(2020, 1, 2, tzinfo=timezone.utc)
RECEIVED = datetime(2026, 1, 1, tzinfo=timezone.utc)
BUILD = RECEIVED + timedelta(days=2)
HASH = hashlib.sha256(b'engineering fixture').hexdigest()
OTHER = hashlib.sha256(b'corrected engineering fixture').hexdigest()


def replace(value, **changes):
    return type(value)(**({key: getattr(value, key) for key in type(value).model_fields} | changes))


def package(*, availability=None, raw_changes=None, attachment_changes=None, review_changes=None):
    raw = Receipt(provider='fixture', source='fixture archive', source_locator='fixture://raw',
                  sha256=HASH, byte_size=19, local_received_at=RECEIVED,
                  source_edition='edition-1', evidence_category='engineering')
    raw = replace(raw, **(raw_changes or {}))
    attachment = Attachment(source_locator='fixture://attestation', sha256=HASH,
                            local_received_at=RECEIVED, description='Fixture only',
                            source_authority_context='No actual source authority')
    attachment = replace(attachment, **(attachment_changes or {}))
    refs = (Ref(attachment_id=attachment.identity, sha256=attachment.sha256),)
    evidence = Evidence(subject_receipt_id=raw.identity, subject_sha256=raw.sha256,
                        source_edition=raw.source_edition or 'unknown', covered_scope='record key fixture-1',
                        covered_fields=('value', 'unit'), revision_semantics='Exact immutable edition only',
                        attachments=refs, availability=availability or Availability(kind='EXACT', exact_at=DECISION))
    review = Review(reviewer='fixture reviewer', reviewed_at=RECEIVED + timedelta(days=1),
                    methodology='Fixture comparison; not market evidence', approved=True,
                    subject_receipt_id=raw.identity, subject_sha256=raw.sha256,
                    availability_evidence_id=evidence.identity, attachments=refs)
    review = replace(review, **(review_changes or {}))
    return Package(raw_receipt=raw, evidence=evidence, review=review, attachments=(attachment,))


def admit(value, **changes):
    return require_historical_evidence(value, **(dict(decision_at=DECISION, research_built_at=BUILD) | changes))


@pytest.mark.parametrize('kind,offset,passes', [
    ('EXACT', -1, True), ('EXACT', 0, True), ('EXACT', 1, False),
    ('BOUNDED_INTERVAL', -1, True), ('BOUNDED_INTERVAL', 0, True),
    ('BOUNDED_INTERVAL', 1, False),
])
def test_availability_cutoff(kind, offset, passes):
    at = DECISION + timedelta(seconds=offset)
    availability = (Availability(kind=kind, exact_at=at) if kind == 'EXACT' else
                    Availability(kind=kind, start=DECISION-timedelta(days=1), end=at))
    value = package(availability=availability)
    if passes:
        assert admit(value) == value
    else:
        with pytest.raises(ValueError, match='not safely known'):
            admit(value)


@pytest.mark.parametrize('kwargs', [
    {}, {'kind': 'UNKNOWN'}, {'kind': 'EXACT'}, {'kind': 'BOUNDED_INTERVAL'},
    {'kind': 'BOUNDED_INTERVAL', 'start': RECEIVED, 'end': DECISION},
    {'kind': 'EXACT', 'exact_at': DECISION, 'end': DECISION},
    {'kind': 'BOUNDED_INTERVAL', 'start': DECISION, 'end': DECISION, 'exact_at': DECISION},
])
def test_unknown_or_invalid_availability(kwargs):
    with pytest.raises(ValueError):
        Availability(**kwargs)


def test_retrospective_clocks_and_immutable_equal_output():
    value = package()
    before = value.model_dump_json()
    assert value.raw_receipt.local_received_at > DECISION
    assert value.review.reviewed_at > DECISION
    assert admit(value) == admit(package()) == value
    assert admit(value) is not value
    assert value.model_dump_json() == before == admit(value).model_dump_json()
    with pytest.raises(ValueError):
        value.review.approved = False
    assert admit(package(availability=Availability(kind='BOUNDED_INTERVAL', start=DECISION, end=DECISION)))


@pytest.mark.parametrize('target', ['raw', 'attachment', 'review'])
def test_build_clock(target):
    changes = {'local_received_at': BUILD + timedelta(seconds=1)}
    kwargs = {f'{target}_changes': changes} if target != 'review' else {
        'review_changes': {'reviewed_at': BUILD + timedelta(seconds=1)}}
    with pytest.raises(ValueError, match='after research build'):
        admit(package(**kwargs))


@pytest.mark.parametrize('target', ['raw', 'attachment'])
def test_review_cannot_precede_reviewed_receipts(target):
    # A reviewer cannot have attested to bytes that had not yet been received.
    late = RECEIVED + timedelta(days=1, seconds=1)
    with pytest.raises(ValueError, match='review precedes'):
        admit(package(**{f'{target}_changes': {'local_received_at': late}}))
    same_instant = RECEIVED + timedelta(days=1)
    assert admit(package(**{f'{target}_changes': {'local_received_at': same_instant}}))


@pytest.mark.parametrize('bad', [datetime(2020, 1, 1), '2020-01-02T00:00:00Z',
                               123, None, DECISION.astimezone(timezone(timedelta(hours=2)))])
@pytest.mark.parametrize('target', ['decision_at', 'research_built_at', 'raw', 'attachment', 'review', 'availability'])
def test_noncanonical_clocks(bad, target):
    with pytest.raises(ValueError):
        if target in ('decision_at', 'research_built_at'):
            admit(package(), **{target: bad})
        elif target == 'availability':
            Availability(kind='EXACT', exact_at=bad)
        elif target == 'review':
            package(review_changes={'reviewed_at': bad})
        else:
            package(**{f'{target}_changes': {'local_received_at': bad}})


@pytest.mark.parametrize('target,changes', [
    ('raw_receipt', {'sha256': OTHER}), ('raw_receipt', {'source_edition': 'edition-2'}),
    ('evidence', {'subject_sha256': OTHER}), ('evidence', {'subject_receipt_id': OTHER}),
    ('evidence', {'source_edition': 'edition-2'}),
    ('review', {'subject_sha256': OTHER}), ('review', {'subject_receipt_id': OTHER}),
    ('review', {'availability_evidence_id': OTHER}), ('review', {'approved': False}),
])
def test_binding_or_approval_failure(target, changes):
    value = package()
    corrupted = value.model_copy(update={target: getattr(value, target).model_copy(update=changes)})
    with pytest.raises(ValueError):
        admit(corrupted)


@pytest.mark.parametrize('bad', ['', ' ', '\n', ' trailing ', '\t'])
@pytest.mark.parametrize('target,field', [('raw', 'provider'), ('raw', 'source'),
                                        ('review', 'reviewer'), ('review', 'methodology'),
                                        ('attachment', 'source_authority_context')])
def test_blank_identities(bad, target, field):
    with pytest.raises(ValueError):
        package(**{f'{target}_changes': {field: bad}})


@pytest.mark.parametrize('changes', [{'sha256': 'x'*64}, {'sha256': HASH.upper()},
                                    {'sha256': 'a'*63}, {'byte_size': 0}, {'byte_size': -1},
                                    {'byte_size': True}, {'source_edition': None}])
def test_raw_invalid(changes):
    with pytest.raises(ValueError):
        package(raw_changes=changes)


@pytest.mark.parametrize('target', ['evidence', 'review'])
@pytest.mark.parametrize('mode', ['hash', 'id', 'missing', 'duplicate'])
def test_attachment_reference_failures(target, mode):
    value = package()
    refs = value.evidence.attachments
    if mode in ('hash', 'id'):
        refs = (replace(refs[0], **{'sha256' if mode == 'hash' else 'attachment_id': OTHER}),)
    elif mode == 'missing':
        refs = ()
    else:
        refs = refs * 2
    corrupt = getattr(value, target).model_copy(update={'attachments': refs})
    with pytest.raises(ValueError):
        admit(value.model_copy(update={target: corrupt}))


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'extra', 'conflicting'])
def test_package_attachment_set(mode):
    value = package()
    attachment = value.attachments[0]
    items = {'missing': (), 'duplicate': (attachment, attachment),
             'extra': (attachment, replace(attachment, description='another proof')),
             'conflicting': (replace(attachment, sha256=OTHER),)}[mode]
    with pytest.raises(ValueError):
        admit(value.model_copy(update={'attachments': items}))


def test_corrections_need_new_proof_and_review():
    old = package()
    revised = package(raw_changes={'sha256': OTHER, 'source_edition': 'edition-2'})
    assert admit(revised)
    assert old.identity != revised.identity
    assert old.evidence.identity != revised.evidence.identity
    assert old.raw_receipt.sha256 == HASH
    with pytest.raises(ValueError):
        admit(old.model_copy(update={'raw_receipt': revised.raw_receipt}))


def test_order_independence_and_semantic_identity():
    value = package()
    attachment = replace(value.attachments[0], description='second supporting attachment')
    items = value.attachments + (attachment,)
    refs = tuple(Ref(attachment_id=item.identity, sha256=item.sha256) for item in items)
    evidence = replace(value.evidence, attachments=refs)
    review = replace(value.review, attachments=refs, availability_evidence_id=evidence.identity)
    a = replace(value, evidence=evidence, review=review, attachments=items)
    b = replace(a, attachments=items[::-1], evidence=replace(evidence, attachments=refs[::-1],
                covered_fields=evidence.covered_fields[::-1]), review=replace(review, attachments=refs[::-1]))
    assert admit(a).model_dump_json() == admit(b).model_dump_json()
    assert a.identity == b.identity
    assert a.identity != value.identity
    assert replace(a, review=replace(review, methodology='Changed methodology')).identity != a.identity
    assert package(availability=Availability(kind='EXACT', exact_at=DECISION-timedelta(seconds=1))).identity != value.identity


@pytest.mark.parametrize('target', ['package', 'raw_receipt', 'evidence', 'review', 'attachments', 'availability', 'references'])
def test_dict_substitution(target):
    value = package()
    if target == 'package':
        value = value.model_dump()
    elif target == 'availability':
        value = value.model_copy(update={'evidence': value.evidence.model_copy(update={
            'availability': value.evidence.availability.model_dump()})})
    elif target == 'references':
        value = value.model_copy(update={'review': value.review.model_copy(update={
            'attachments': (value.review.attachments[0].model_dump(),)})})
    else:
        item = getattr(value, target)
        replacement = (item[0].model_dump(),) if target == 'attachments' else item.model_dump()
        value = value.model_copy(update={target: replacement})
    with pytest.raises(ValueError):
        admit(value)


@pytest.mark.parametrize('mode', ['construct', 'nested', 'version', 'list'])
def test_bypasses_revalidated(mode):
    value = package()
    if mode == 'construct':
        value = value.model_copy(update={'evidence': Evidence.model_construct()})
    elif mode == 'nested':
        object.__setattr__(value.evidence.availability, 'exact_at', None)
    elif mode == 'version':
        value = value.model_copy(update={'schema_version': 'historical-evidence-package-v99'})
    else:
        value = value.model_copy(update={'attachments': list(value.attachments)})
    with pytest.raises((ValueError, AttributeError)):
        admit(value)


def test_no_io_or_provider_or_universe_work(monkeypatch):
    import app.research.historical_evidence as module
    from app.storage.database import Database
    from app.storage.reference_repository import ReferenceRepository
    from app.data.raw_store import ImmutableRawStore

    def forbidden(*args, **kwargs):
        pytest.fail('R1.1 must not perform IO, provider, or reference work')

    from app.data.providers.egid import EGIDProvider
    from app.data.providers.eodhd import EODHDProvider
    from app.data.providers.egx_official import EGXOfficialPublicProvider

    source = module.__loader__.get_source(module.__name__)
    value = package()
    for owner, name in [(socket, 'socket'), (socket, 'create_connection'),
                        (sqlite3, 'connect'), (Database, 'connect'),
                        (builtins, 'open'), (Path, 'open'),
                        (ReferenceRepository, 'universe'), (ReferenceRepository, 'ingest'),
                        (ImmutableRawStore, 'store_bytes'),
                        (EGIDProvider, '__init__'), (EGIDProvider, 'fetch_daily_bars'),
                        (EODHDProvider, '__init__'), (EODHDProvider, 'fetch_daily_bars'),
                        (EGXOfficialPublicProvider, '__init__'),
                        (EGXOfficialPublicProvider, 'fetch_index_bars')]:
        monkeypatch.setattr(owner, name, forbidden)
    assert admit(value) == value
    # Narrow dependency boundary: no provider/storage/calendar/universe imports or calls.
    import ast
    tree = ast.parse(source)
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert imports == ['datetime', 'typing', 'pydantic', 'app.strategies.contracts', 'models']


def test_cannot_masquerade_as_m3_input():
    from app.data.point_in_time import PointInTimeDailyRepository, PointInTimeDailyDataset
    from app.data.reference import UniverseEvidence
    value = package()
    assert not isinstance(value, PointInTimeDailyDataset)
    with pytest.raises(ValueError):
        UniverseEvidence.model_validate(value)
    with pytest.raises(TypeError):
        PointInTimeDailyRepository(None).load(package=value, as_of=DECISION)
