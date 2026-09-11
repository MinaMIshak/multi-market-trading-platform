"""R1.1 offline metadata/attestation contracts; no acquisition or M3 admission."""
import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, field_validator, model_validator

from app.strategies.contracts import Contract
from .models import _canonical

Text = Annotated[str, Field(min_length=1, pattern=r'^\S(?:.*\S)?$')]
SHA256 = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


def _timestamp(value):
    if type(value) is not datetime or value.tzinfo != timezone.utc:
        raise ValueError('canonical UTC datetime required')
    return value


UTC = Annotated[datetime, BeforeValidator(_timestamp)]


class _Identified(Contract):
    @property
    def identity(self) -> str:
        """SHA256 of sorted-key compact UTF-8 JSON, including schema versions."""
        content = json.dumps(self.model_dump(mode='json'), sort_keys=True,
                             separators=(',', ':'), ensure_ascii=True, allow_nan=False)
        return hashlib.sha256(content.encode('utf-8')).hexdigest()


class HistoricalRawReceipt(_Identified):
    schema_version: Literal['historical-raw-receipt-v1'] = 'historical-raw-receipt-v1'
    provider: Text
    source: Text
    source_locator: Text
    sha256: SHA256
    byte_size: int = Field(gt=0)
    local_received_at: UTC
    source_edition: Text | None  # Unknown is representable, but cannot be admitted.
    evidence_category: Text


class HistoricalEvidenceAttachment(_Identified):
    schema_version: Literal['historical-evidence-attachment-v1'] = 'historical-evidence-attachment-v1'
    source_locator: Text
    sha256: SHA256
    local_received_at: UTC
    description: Text
    source_authority_context: Text


class HistoricalAttachmentReference(Contract):
    schema_version: Literal['historical-attachment-reference-v1'] = 'historical-attachment-reference-v1'
    attachment_id: SHA256
    sha256: SHA256


def _references(value):
    if type(value) is not tuple:
        raise ValueError('canonical reference tuple required')
    refs = tuple(_canonical(HistoricalAttachmentReference, item) for item in value)
    if len({item.attachment_id for item in refs}) != len(refs):
        raise ValueError('duplicate attachment identity')
    return tuple(sorted(refs, key=lambda item: item.attachment_id))


class HistoricalAvailability(_Identified):
    schema_version: Literal['historical-availability-v1'] = 'historical-availability-v1'
    kind: Literal['EXACT', 'BOUNDED_INTERVAL']
    exact_at: UTC | None = None
    start: UTC | None = None
    end: UTC | None = None

    @model_validator(mode='after')
    def shape(self):
        if self.kind == 'EXACT':
            if self.exact_at is None or self.start is not None or self.end is not None:
                raise ValueError('EXACT requires only exact_at')
        elif (self.exact_at is not None or self.start is None or self.end is None
              or self.start > self.end):
            raise ValueError('BOUNDED_INTERVAL requires ordered inclusive start/end')
        return self


class HistoricalAvailabilityEvidence(_Identified):
    schema_version: Literal['historical-availability-evidence-v1'] = 'historical-availability-evidence-v1'
    subject_receipt_id: SHA256
    subject_sha256: SHA256
    source_edition: Text
    covered_scope: Text
    covered_fields: tuple[Text, ...] = Field(min_length=1)
    revision_semantics: Text
    attachments: tuple[HistoricalAttachmentReference, ...] = Field(min_length=1)
    availability: HistoricalAvailability

    _refs = field_validator('attachments', mode='before')(_references)

    @field_validator('availability', mode='before')
    @classmethod
    def canonical_availability(cls, value):
        return _canonical(HistoricalAvailability, value)

    @field_validator('covered_fields')
    @classmethod
    def fields(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('duplicate covered field')
        return tuple(sorted(value))


class HistoricalSourceReview(_Identified):
    """Trusted attestation, not cryptographic proof of source authenticity."""
    schema_version: Literal['historical-source-review-v1'] = 'historical-source-review-v1'
    reviewer: Text
    reviewed_at: UTC
    methodology: Text
    approved: bool
    subject_receipt_id: SHA256
    subject_sha256: SHA256
    availability_evidence_id: SHA256
    attachments: tuple[HistoricalAttachmentReference, ...] = Field(min_length=1)

    _refs = field_validator('attachments', mode='before')(_references)


class HistoricalEvidencePackage(_Identified):
    schema_version: Literal['historical-evidence-package-v1'] = 'historical-evidence-package-v1'
    raw_receipt: HistoricalRawReceipt
    evidence: HistoricalAvailabilityEvidence
    review: HistoricalSourceReview
    attachments: tuple[HistoricalEvidenceAttachment, ...] = Field(min_length=1)

    @field_validator('raw_receipt', 'evidence', 'review', mode='before')
    @classmethod
    def canonical_members(cls, value, info):
        kind = {'raw_receipt': HistoricalRawReceipt,
                'evidence': HistoricalAvailabilityEvidence,
                'review': HistoricalSourceReview}[info.field_name]
        return _canonical(kind, value)

    @field_validator('attachments', mode='before')
    @classmethod
    def canonical_attachments(cls, value):
        if type(value) is not tuple:
            raise ValueError('canonical attachment tuple required')
        items = tuple(_canonical(HistoricalEvidenceAttachment, item) for item in value)
        if len({item.identity for item in items}) != len(items):
            raise ValueError('duplicate attachment identity')
        return tuple(sorted(items, key=lambda item: item.identity))

    @model_validator(mode='after')
    def bindings(self):
        raw, evidence, review = self.raw_receipt, self.evidence, self.review
        for claim in (evidence, review):
            if (claim.subject_receipt_id != raw.identity
                    or claim.subject_sha256 != raw.sha256):
                raise ValueError('subject receipt identity/hash mismatch')
        if raw.source_edition != evidence.source_edition:
            raise ValueError('source edition mismatch or unknown')
        if review.availability_evidence_id != evidence.identity:
            raise ValueError('review availability evidence mismatch')
        expected = _references(tuple(HistoricalAttachmentReference(
            attachment_id=item.identity, sha256=item.sha256) for item in self.attachments))
        if evidence.attachments != expected or review.attachments != expected:
            raise ValueError('attachment references must match exact package set and hashes')
        return self


def require_historical_evidence(package, *, decision_at, research_built_at):
    """Reconstruct and admit one exact reviewed scope; never admit operational M3 data."""
    package = _canonical(HistoricalEvidencePackage, package)
    decision_at = _timestamp(decision_at)
    research_built_at = _timestamp(research_built_at)
    if package.raw_receipt.local_received_at > research_built_at:
        raise ValueError('raw receipt after research build')
    if any(item.local_received_at > research_built_at for item in package.attachments):
        raise ValueError('attachment receipt after research build')
    if package.review.reviewed_at > research_built_at:
        raise ValueError('review after research build')
    if not package.review.approved:
        raise ValueError('approved review required')
    availability = package.evidence.availability
    latest = availability.exact_at if availability.kind == 'EXACT' else availability.end
    if latest > decision_at:
        raise ValueError('historical availability not safely known by decision')
    return package
