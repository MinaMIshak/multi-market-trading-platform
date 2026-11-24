import hashlib
import json

from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from app.data.egx_universe_changes import (
    EGXUniverseChanges,
    compare_egx_universes,
    universe_sha256,
)
from app.data.reference import UniverseEvidence, UniverseMember


NOW = datetime(2026, 11, 23, 20, 30, tzinfo=timezone.utc)

COMI = UUID("00000000-0000-0000-0000-000000000001")
HRHO = UUID("00000000-0000-0000-0000-000000000002")
OLDX = UUID("00000000-0000-0000-0000-000000000003")
NEWX = UUID("00000000-0000-0000-0000-000000000004")
RIGHT = UUID("00000000-0000-0000-0000-000000000005")


def universe(day, members):
    return UniverseEvidence(
        contract="egx-universe-v1",
        market="EGX",
        effective_date=day,
        published_at=NOW,
        complete=True,
        members=tuple(
            UniverseMember(instrument_id=i, symbol=s, eligible=e)
            for i, s, e in members
        ),
    )


PREVIOUS = universe(
    date(2026, 11, 22),
    [
        (COMI, "COMI", True),
        (HRHO, "HRHO", True),
        (OLDX, "OLDX", True),
        (RIGHT, "KORA_R1", False),
    ],
)


def test_reports_each_change_kind_by_identity():
    current = universe(
        date(2026, 11, 23),
        [
            (COMI, "COMI", True),
            (HRHO, "HRHE", True),  # same identity, new symbol
            (NEWX, "NEWX", True),  # new identity
            (RIGHT, "KORA_R1", True),  # eligibility flip
        ],
    )

    changes = compare_egx_universes(PREVIOUS, current)

    assert [(c.instrument_id, c.symbol) for c in changes.added] == [
        (NEWX, "NEWX")
    ]
    assert [(c.instrument_id, c.symbol) for c in changes.absent] == [
        (OLDX, "OLDX")
    ]
    assert [
        (c.instrument_id, c.previous_symbol, c.current_symbol)
        for c in changes.symbol_changed
    ] == [(HRHO, "HRHO", "HRHE")]
    assert [
        (c.instrument_id, c.previous_eligible, c.current_eligible)
        for c in changes.eligibility_changed
    ] == [(RIGHT, False, True)]
    assert changes.symbol_reassigned == ()
    assert changes.unchanged_count == 1
    assert changes.previous_member_count == 4
    assert changes.current_member_count == 4


def test_interpretation_and_safety_labels_are_fixed():
    current = universe(
        date(2026, 11, 23),
        [(COMI, "COMI", True), (HRHO, "HRHO", True)],
    )

    changes = compare_egx_universes(PREVIOUS, current)

    assert changes.contract_version == "egx-universe-changes-v1"
    assert changes.identity_basis == "CANONICAL_INSTRUMENT_ID"
    assert changes.listing_interpretation == "NOT_ESTABLISHED"
    assert changes.delisting_interpretation == "NOT_ESTABLISHED"
    assert changes.symbol_change_interpretation == "NOT_ESTABLISHED"
    assert changes.intermediate_dates == "NOT_OBSERVED"
    assert changes.source_review == "NOT_CHECKED"
    assert changes.trading_effect == "NONE"
    assert changes.mode == "SHADOW"
    assert changes.live_money is False


def test_symbol_reused_by_different_identity_is_not_a_rename():
    current = universe(
        date(2026, 11, 23),
        [
            (COMI, "COMI", True),
            (HRHO, "HRHO", True),
            (NEWX, "OLDX", True),  # OLDX symbol now bound to NEWX
            (RIGHT, "KORA_R1", False),
        ],
    )

    changes = compare_egx_universes(PREVIOUS, current)

    assert changes.symbol_changed == ()
    assert [c.instrument_id for c in changes.added] == [NEWX]
    assert [c.instrument_id for c in changes.absent] == [OLDX]
    assert [
        (c.symbol, c.previous_instrument_id, c.current_instrument_id)
        for c in changes.symbol_reassigned
    ] == [("OLDX", OLDX, NEWX)]


def test_member_order_does_not_change_result():
    members = [
        (NEWX, "NEWX", True),
        (COMI, "COMI", True),
        (HRHO, "HRHE", True),
    ]
    a = universe(date(2026, 11, 23), members)
    b = universe(date(2026, 11, 23), list(reversed(members)))

    assert (
        compare_egx_universes(PREVIOUS, a).added
        == compare_egx_universes(PREVIOUS, b).added
    )
    assert (
        compare_egx_universes(PREVIOUS, a).symbol_changed
        == compare_egx_universes(PREVIOUS, b).symbol_changed
    )


def test_digest_matches_publisher_canonical_bytes():
    payload = json.dumps(
        PREVIOUS.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()

    assert universe_sha256(PREVIOUS) == hashlib.sha256(payload).hexdigest()

    current = universe(date(2026, 11, 23), [(COMI, "COMI", True)])
    changes = compare_egx_universes(PREVIOUS, current)

    assert changes.previous_universe_sha256 == universe_sha256(PREVIOUS)
    assert changes.current_universe_sha256 == universe_sha256(current)


@pytest.mark.parametrize(
    "day",
    [date(2026, 11, 22), date(2026, 11, 21)],
)
def test_non_increasing_effective_dates_fail_closed(day):
    current = universe(day, [(COMI, "COMI", True)])

    with pytest.raises(ValueError, match="must precede"):
        compare_egx_universes(PREVIOUS, current)


def test_requires_exact_universe_evidence():
    with pytest.raises(TypeError, match="exact UniverseEvidence"):
        compare_egx_universes(PREVIOUS.model_dump(), PREVIOUS)


def test_model_construct_bypass_is_revalidated():
    forged = UniverseEvidence.model_construct(
        contract="egx-universe-v1",
        market="EGX",
        effective_date=date(2026, 11, 23),
        published_at=NOW,
        complete=True,
        members=(
            UniverseMember(instrument_id=COMI, symbol="COMI", eligible=True),
            UniverseMember(instrument_id=COMI, symbol="CIB", eligible=True),
        ),
    )

    with pytest.raises(ValueError, match="duplicate"):
        compare_egx_universes(PREVIOUS, forged)


def test_result_contract_rejects_inconsistent_counts():
    current = universe(date(2026, 11, 23), [(COMI, "COMI", True)])
    changes = compare_egx_universes(PREVIOUS, current)
    data = changes.model_dump()
    data["unchanged_count"] = data["unchanged_count"] + 1

    with pytest.raises(ValueError, match="unchanged count"):
        EGXUniverseChanges.model_validate(data)
