"""Pure retained-input preflight; passing is not admission or source review."""

from datetime import date, datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

from app.data.models import DataAssetType, IngestionStatus, RawArtifactManifest


def check_retained_egid_input(
    manifest: RawArtifactManifest,
    raw: bytes,
    *,
    status: IngestionStatus,
    completed_at: datetime | None,
    as_of: datetime,
) -> date:
    """Return only the recorded date when basic retained provenance is usable.

    Does not parse membership, establish source authority/permission, admit an
    artifact, publish a universe or create review evidence. Receipt time is
    never substituted for the effective date. No IO or database mutation.
    """
    manifest = RawArtifactManifest.model_validate(manifest.model_dump())
    if not isinstance(raw, bytes):
        raise ValueError("retained bytes required")
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError("aware as_of required")
    if manifest.provider != "egid" or manifest.asset_type != DataAssetType.SECURITY_MASTER:
        raise ValueError("EGID security-master input required")
    if manifest.symbol is not None or manifest.granularity is not None:
        raise ValueError("whole security-master snapshot required")
    if sha256(raw).hexdigest() != manifest.sha256 or len(raw) != manifest.byte_size:
        raise ValueError("retained digest or size mismatch")
    if manifest.market_date is None:
        raise ValueError("effective date not established")
    if status != IngestionStatus.VALIDATED or completed_at is None:
        raise ValueError("completed validated ingestion required")
    if not isinstance(completed_at, datetime) or completed_at.utcoffset() is None:
        raise ValueError("aware completion required")
    if manifest.received_at.utcoffset() is None:
        raise ValueError("aware receipt required")
    if not manifest.received_at <= completed_at <= as_of:
        raise ValueError("ingestion chronology invalid or unavailable at as_of")
    if manifest.market_date > manifest.received_at.astimezone(ZoneInfo("Africa/Cairo")).date():
        raise ValueError("effective date after receipt")
    return manifest.market_date
