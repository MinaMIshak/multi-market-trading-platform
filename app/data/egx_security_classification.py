"""Conservative EGX security-class attribution.

This layer is deliberately separate from InstrumentType.

InstrumentType answers a coarse identity question:
    EQUITY / INDEX / UNKNOWN

SecurityClass answers what kind of EGX security the admitted identity
appears to represent for downstream universe-policy decisions.

Classification is deterministic and fail-closed where evidence conflicts.
It does not establish dated exchange membership or trading eligibility.
"""

from __future__ import annotations

import re
from enum import StrEnum

from app.data.security_master import (
    CanonicalInstrument,
    InstrumentType,
)


class SecurityClass(StrEnum):
    ORDINARY_SHARE_CANDIDATE = "ORDINARY_SHARE_CANDIDATE"
    PREFERRED_SHARE = "PREFERRED_SHARE"
    ETF = "ETF"
    FUND = "FUND"
    FUND_CERTIFICATE = "FUND_CERTIFICATE"
    SPAC = "SPAC"
    SUBSCRIPTION_RIGHT = "SUBSCRIPTION_RIGHT"
    INDEX = "INDEX"
    UNKNOWN = "UNKNOWN"


# Exact reviewed identity overrides from the admitted EGID snapshot.
#
# Keep this mapping narrow. A new special security must not be inferred from
# a fuzzy name match and silently become production-eligible.
_SPECIAL_BY_SYMBOL_CODE: dict[str, SecurityClass] = {
    # Arab Land Direct Mutual Fund
    "EGMKM001DM19": SecurityClass.FUND,

    # QALA For Financial Investments - Preferred Shares
    "EGS73541P048": SecurityClass.PREFERRED_SHARE,

    # Egyptians Real Estate Fund Certificates
    "EGS694B1C017": SecurityClass.FUND_CERTIFICATE,

    # EGX 30 INDEX ETF
    "EGS69491M015": SecurityClass.ETF,

    # Certificates Of Odin Egyptian Equity Investment Fund-KASAB
    "EGS696Z1C017": SecurityClass.FUND_CERTIFICATE,

    # Grova Special Purpose Acquisition Company
    "EGS69921C012": SecurityClass.SPAC,

    # O G Capital For Investments SPAC
    "EGS699Z1C014": SecurityClass.SPAC,
}


_RIGHT_TICKER = re.compile(
    r"^[A-Z0-9][A-Z0-9._-]*_R[0-9]+$"
)


def classify_egx_security(
    instrument: CanonicalInstrument,
) -> SecurityClass:
    """Return conservative security-class attribution.

    This function does not establish:
    - authoritative dated EGX membership,
    - source-rights approval,
    - market-data readiness,
    - trading eligibility.

    Subscription-right classification requires both independent fields to
    agree: canonical ticker shape and English security name. If they conflict,
    fail closed as UNKNOWN.
    """

    if not isinstance(
        instrument,
        CanonicalInstrument,
    ):
        raise TypeError(
            "CanonicalInstrument required"
        )

    if (
        instrument.instrument_type
        is InstrumentType.INDEX
    ):
        return SecurityClass.INDEX

    if (
        instrument.instrument_type
        is not InstrumentType.EQUITY
    ):
        return SecurityClass.UNKNOWN

    ticker = (
        instrument.canonical_ticker
        .strip()
        .upper()
    )

    name = (
        instrument.name_en
        or ""
    ).strip().upper()

    symbol_code = (
        instrument.source_symbol_code
        .strip()
        .upper()
    )

    ticker_says_right = (
        _RIGHT_TICKER.fullmatch(
            ticker
        )
        is not None
    )

    name_says_right = (
        name.startswith(
            "SUBSCRIPTION RIGHTS OF "
        )
    )

    if (
        ticker_says_right
        and name_says_right
    ):
        return (
            SecurityClass
            .SUBSCRIPTION_RIGHT
        )

    if (
        ticker_says_right
        != name_says_right
    ):
        return SecurityClass.UNKNOWN

    special = (
        _SPECIAL_BY_SYMBOL_CODE
        .get(symbol_code)
    )

    if special is not None:
        return special

    return (
        SecurityClass
        .ORDINARY_SHARE_CANDIDATE
    )


def is_rehearsal_ordinary_candidate(
    classification: SecurityClass,
) -> bool:
    """Mechanical M5 candidate gate, not production eligibility approval."""

    if not isinstance(
        classification,
        SecurityClass,
    ):
        raise TypeError(
            "SecurityClass required"
        )

    return (
        classification
        is SecurityClass
        .ORDINARY_SHARE_CANDIDATE
    )
