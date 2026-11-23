"""Publish an exact admitted EGID snapshot as dated EGX universe evidence.

Trust boundaries:

- requires an exact immutable security-master admission,
- requires its exact retained post-commit observation,
- never creates or backdates an observation,
- never chooses "latest",
- delegates classification to the pure universe builder,
- publishes only egx-universe-v1,
- never creates egx-source-review-v1,
- never enables live money.
"""

from __future__ import annotations

import json
import re

from dataclasses import dataclass
from datetime import date, datetime

from app.data.egx_universe_builder import (
    build_egx_universe_candidate,
)
from app.data.security_master import (
    CanonicalInstrument,
)
from app.storage.reference_repository import (
    ReferenceRepository,
)
from app.storage.security_master_repository import (
    SecurityMasterRepository,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")

PUBLISHER_PROVIDER = (
    "egx-universe-producer-v1"
)


@dataclass(frozen=True)
class EGXUniversePublication:
    snapshot_date: date
    artifact_sha256: str
    observation_sha256: str
    ingestion_id: str
    universe_sha256: str
    member_count: int
    eligible_count: int
    ineligible_count: int
    live_money: bool = False


def publish_egx_universe(
    *,
    security_master: SecurityMasterRepository,
    references: ReferenceRepository,
    snapshot_date: date,
    artifact_sha256: str,
) -> EGXUniversePublication:
    """Publish one exact candidate universe.

    Source approval is intentionally outside this function. A resulting
    egx-universe-v1 remains unusable by the PIT reader until independently
    reviewed through egx-source-review-v1.
    """

    if type(snapshot_date) is not date:
        raise TypeError(
            "exact snapshot date required"
        )

    if (
        type(artifact_sha256) is not str
        or _SHA256.fullmatch(
            artifact_sha256
        )
        is None
    ):
        raise ValueError(
            "lowercase sha256 required"
        )

    admission = (
        security_master.get_admission(
            provider="egid",
            snapshot_date=snapshot_date,
            artifact_sha256=artifact_sha256,
        )
    )

    # Important: this is a READ of previously retained evidence.
    # It must never call retain_admission_observation().
    observation = (
        security_master
        .get_admission_observation(
            provider="egid",
            snapshot_date=snapshot_date,
            artifact_sha256=artifact_sha256,
        )
    )

    observed_receipt = json.loads(
        observation["receipt_json"]
    )

    if observed_receipt != admission:
        raise ValueError(
            "admission observation mismatch"
        )

    if (
        admission.get("provider")
        != "egid"
        or admission.get("snapshot_date")
        != snapshot_date.isoformat()
        or admission.get(
            "artifact_sha256"
        )
        != artifact_sha256
        or admission.get("live_money")
        is not False
        or observation.get("live_money")
        is not False
    ):
        raise ValueError(
            "invalid EGID admission binding"
        )

    raw_instruments = admission.get(
        "instruments"
    )

    if (
        type(raw_instruments) is not list
        or admission.get(
            "instrument_count"
        )
        != len(raw_instruments)
    ):
        raise ValueError(
            "invalid admitted instrument set"
        )

    instruments = tuple(
        CanonicalInstrument.model_validate(
            item
        )
        for item in raw_instruments
    )

    if any(
        item.source_provider != "egid"
        for item in instruments
    ):
        raise ValueError(
            "non-EGID instrument in admission"
        )

    published_at = datetime.fromisoformat(
        observation["observed_at"]
    )

    if published_at.utcoffset() is None:
        raise ValueError(
            "naive admission observation time"
        )

    universe = (
        build_egx_universe_candidate(
            instruments=instruments,
            effective_date=snapshot_date,
            published_at=published_at,
        )
    )

    payload = json.dumps(
        universe.model_dump(
            mode="json"
        ),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()

    source_uri = (
        "internal://security-master/"
        "egid/"
        + snapshot_date.isoformat()
        + "/"
        + artifact_sha256
        + "/observation/"
        + observation[
            "receipt_sha256"
        ]
    )

    manifest = references.ingest(
        provider=PUBLISHER_PROVIDER,
        payload=payload,
        source_uri=source_uri,
        contract="egx-universe-v1",
    )

    eligible_count = sum(
        member.eligible
        for member in universe.members
    )

    member_count = len(
        universe.members
    )

    return EGXUniversePublication(
        snapshot_date=snapshot_date,
        artifact_sha256=artifact_sha256,
        observation_sha256=(
            observation[
                "receipt_sha256"
            ]
        ),
        ingestion_id=str(
            manifest.ingestion_id
        ),
        universe_sha256=(
            manifest.sha256
        ),
        member_count=member_count,
        eligible_count=eligible_count,
        ineligible_count=(
            member_count
            - eligible_count
        ),
        live_money=False,
    )
