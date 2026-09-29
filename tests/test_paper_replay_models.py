"""Direct model-level contracts for app/paper/replay_models.py PaperReplayPosition.

PaperReplayInput.consistency already fails closed when a bar's
instrument/venue/symbol diverges from the request's declared identity
(test_stable_instrument_binding_is_fail_closed in test_m6_1_paper_replay.py).
PaperReplayPosition is constructed only from same-request bars in the real
engine (app/paper/replay.py), so this gap is unreachable through that path,
but the Contract itself is meant to be standalone-sound: nothing prevented a
directly constructed CLOSED position from pairing an entry and exit fill for
two different instruments/venues/symbols.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.paper.replay_models import PaperReplayFill, PaperReplayPosition


D = Decimal

INSTRUMENT = "fixture-instrument"
VENUE = "XNAS"
SYMBOL = "SWDY"

INTERVAL_START = datetime(2024, 7, 8, 13, 30, tzinfo=timezone.utc)
INTERVAL_END = INTERVAL_START + timedelta(minutes=1)


def fill(*, side, sequence, symbol=SYMBOL, instrument_id=INSTRUMENT, venue_id=VENUE, **changes):
    values = dict(
        side=side,
        quantity=10,
        raw_price=D("10"),
        price=D("10"),
        instrument_id=instrument_id,
        venue_id=venue_id,
        symbol=symbol,
        session_id="s1",
        market_date=date(2024, 7, 8),
        bar_sequence=sequence,
        interval_start_utc=INTERVAL_START,
        interval_end_utc=INTERVAL_END,
        known_at_utc=INTERVAL_END,
        source_id="fixture-source",
        provenance_id="fixture-provenance",
        at_open=False,
    )
    values.update(changes)
    return PaperReplayFill(**values)


def test_closed_position_entry_and_exit_must_share_identity():
    entry = fill(side="BUY", sequence=1)
    for mismatch in ("instrument_id", "venue_id", "symbol"):
        exit_fill = fill(
            side="SELL",
            sequence=2,
            known_at_utc=INTERVAL_END + timedelta(minutes=1),
            **{mismatch: "other-" + getattr(entry, mismatch)},
        )
        with pytest.raises(ValueError, match="instrument/venue/symbol"):
            PaperReplayPosition(
                trade_plan_id=UUID(int=1),
                quantity=10,
                entry=entry,
                exit=exit_fill,
                state="CLOSED",
            )


def test_closed_position_accepts_matching_identity():
    entry = fill(side="BUY", sequence=1)
    exit_fill = fill(
        side="SELL",
        sequence=2,
        known_at_utc=INTERVAL_END + timedelta(minutes=1),
    )
    position = PaperReplayPosition(
        trade_plan_id=UUID(int=1),
        quantity=10,
        entry=entry,
        exit=exit_fill,
        state="CLOSED",
    )
    assert position.state == "CLOSED"
