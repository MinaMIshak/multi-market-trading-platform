"""Build a conservative dated EGX universe from an exact admitted snapshot.

This module is pure:
- no database access,
- no network access,
- no file writes,
- no source-review approval.

It converts identity/security-class evidence into a candidate dated universe.
ReferenceRepository review remains a separate trust boundary.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from app.data.egx_security_classification import (
    SecurityClass,
    classify_egx_security,
)
from app.data.reference import (
    UniverseEvidence,
    UniverseMember,
)
from app.data.security_master import (
    CanonicalInstrument,
)


INELIGIBLE_SECURITY_CLASSES = frozenset(
    {
        SecurityClass.PREFERRED_SHARE,
        SecurityClass.ETF,
        SecurityClass.FUND,
        SecurityClass.FUND_CERTIFICATE,
        SecurityClass.SPAC,
        SecurityClass.SUBSCRIPTION_RIGHT,
    }
)


def build_egx_universe_candidate(
    *,
    instruments: Iterable[CanonicalInstrument],
    effective_date: date,
    published_at: datetime,
) -> UniverseEvidence:
    """Build deterministic candidate universe evidence.

    ORDINARY_SHARE_CANDIDATE is mechanically eligible for the current
    ordinary-share scanner policy.

    This is not, by itself, authoritative EGX membership or source review.
    The resulting evidence still requires the existing independent
    egx-source-review-v1 control before PIT consumption.
    """

    instruments = tuple(instruments)

    if not instruments:
        raise ValueError(
            "non-empty admitted security master required"
        )

    members: list[UniverseMember] = []

    seen_ids = set()
    seen_symbols = set()

    for instrument in sorted(
        instruments,
        key=lambda item: (
            item.canonical_ticker,
            str(item.instrument_id),
        ),
    ):
        if instrument.instrument_id in seen_ids:
            raise ValueError(
                "duplicate admitted instrument identity"
            )

        seen_ids.add(
            instrument.instrument_id
        )

        classification = (
            classify_egx_security(
                instrument
            )
        )

        if classification is SecurityClass.INDEX:
            continue

        if classification is SecurityClass.UNKNOWN:
            raise ValueError(
                "unknown EGX security class: "
                + instrument.canonical_ticker
            )

        symbol = (
            instrument.canonical_ticker
            .strip()
            .upper()
        )

        if symbol in seen_symbols:
            raise ValueError(
                "duplicate universe symbol: "
                + symbol
            )

        seen_symbols.add(symbol)

        if (
            classification
            is SecurityClass
            .ORDINARY_SHARE_CANDIDATE
        ):
            eligible = True

        elif (
            classification
            in INELIGIBLE_SECURITY_CLASSES
        ):
            eligible = False

        else:
            raise ValueError(
                "unhandled EGX security class: "
                + classification.value
            )

        members.append(
            UniverseMember(
                instrument_id=(
                    instrument.instrument_id
                ),
                symbol=symbol,
                eligible=eligible,
            )
        )

    if not members:
        raise ValueError(
            "EGX universe contains no securities"
        )

    if not any(
        member.eligible
        for member in members
    ):
        raise ValueError(
            "EGX universe contains no eligible securities"
        )

    return UniverseEvidence(
        contract="egx-universe-v1",
        market="EGX",
        effective_date=effective_date,
        published_at=published_at,
        complete=True,
        members=tuple(members),
    )
