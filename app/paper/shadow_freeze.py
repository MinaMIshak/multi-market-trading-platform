"""Local forward-only document preservation; never trading admission or execution."""
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


LABEL = 'EXPERIMENTAL / PAPER ONLY'


def _now():
    return datetime.now(timezone.utc)


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo != timezone.utc:
        raise ValueError('canonical UTC datetime required')
    return value


def freeze_document(directory: Path, *, record_id: str, market: str,
                    information_cutoff: datetime, decision_cutoff: datetime,
                    document: bytes) -> Path:
    """Publish once, with exact document bytes, using the local wall clock.

    Cutoffs are caller assertions, not verified session/calendar truth. Every
    result remains UNADMITTED and NOT SCORED. The directory must be a trusted
    local research directory. No caller-supplied freeze timestamp is accepted.
    """
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', record_id):
        raise ValueError('invalid record_id')
    if market not in ('EGX', 'US'):
        raise ValueError('unsupported market')
    _utc(information_cutoff)
    _utc(decision_cutoff)
    if type(document) is not bytes or not document:
        raise ValueError('nonempty original document bytes required')
    received_at = _now()
    if not information_cutoff <= received_at < decision_cutoff:
        raise ValueError('future information cutoff or missed decision cutoff')
    envelope = {
        'schema_version': 'shadow-document-v1', 'label': LABEL,
        'admission': 'UNADMITTED', 'scoring': 'NOT SCORED',
        'record_id': record_id, 'market': market,
        'information_cutoff': information_cutoff.isoformat(),
        'decision_cutoff': decision_cutoff.isoformat(),
        'received_at': received_at.isoformat(),
        'document_hex': document.hex(),
        'document_sha256': hashlib.sha256(document).hexdigest(),
        'document_size': len(document),
    }
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f'{record_id}.json'
    # Same-filesystem hard link gives atomic create-if-absent, including races.
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=directory)
    published = False
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps(envelope, sort_keys=True).encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        if not received_at <= _now() < decision_cutoff:
            raise ValueError('clock rollback or missed decision cutoff during write')
        os.link(temporary, destination)
        published = True
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if not received_at <= _now() < decision_cutoff:
            raise ValueError('clock rollback or missed decision cutoff during publication')
    except BaseException:
        if published:
            destination.unlink()
        raise
    finally:
        os.unlink(temporary)
    return destination


def audit_document(path: Path) -> dict:
    """Verify stored bytes and clock ordering, without granting scoreability.

    Hashes detect accidental changes; local files are not external timestamp
    attestations and cannot prove absence of deliberate replacement/backdating.
    """
    envelope = json.loads(Path(path).read_bytes())
    expected = {'schema_version', 'label', 'admission', 'scoring', 'record_id',
                'market', 'information_cutoff', 'decision_cutoff', 'received_at',
                'document_hex', 'document_sha256', 'document_size'}
    if set(envelope) != expected:
        raise ValueError('unexpected envelope fields')
    if (envelope['schema_version'] != 'shadow-document-v1'
            or envelope['label'] != LABEL or envelope['admission'] != 'UNADMITTED'
            or envelope['scoring'] != 'NOT SCORED'):
        raise ValueError('invalid shadow status')
    if (envelope['market'] not in ('EGX', 'US')
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', envelope['record_id'])
            or Path(path).name != envelope['record_id'] + '.json'):
        raise ValueError('invalid record identity')
    document = bytes.fromhex(envelope['document_hex'])
    if (not document or type(envelope['document_size']) is not int
            or len(document) != envelope['document_size']
            or hashlib.sha256(document).hexdigest() != envelope['document_sha256']):
        raise ValueError('document integrity failure')
    cutoff, received, decision = (
        _utc(datetime.fromisoformat(envelope[key]))
        for key in ('information_cutoff', 'received_at', 'decision_cutoff'))
    if not cutoff <= received < decision:
        raise ValueError('invalid clock ordering')
    return envelope
