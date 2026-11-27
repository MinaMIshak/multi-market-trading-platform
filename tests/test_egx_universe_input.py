from datetime import date, datetime, timedelta, timezone
from hashlib import sha256

import pytest

from app.data.egx_universe_input import check_retained_egid_input
from app.data.models import RawArtifactManifest


NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
RAW = b"unit-test-only bytes; not market evidence"


def check(*, changes=None, raw=RAW, status="VALIDATED", completed_at=NOW, as_of=NOW):
    fields = dict(provider="egid", asset_type="SECURITY_MASTER", raw_path="fixture.json",
                  sha256=sha256(RAW).hexdigest(), byte_size=len(RAW),
                  received_at=NOW, market_date=date(2026, 11, 26))
    fields.update(changes or {})
    return check_retained_egid_input(RawArtifactManifest(**fields), raw,
                                     status=status, completed_at=completed_at, as_of=as_of)


def test_returns_recorded_date_not_receipt_date():
    assert check() == date(2026, 11, 26)


@pytest.mark.parametrize("kwargs,match", [
    ({"changes": {"market_date": None}}, "effective date not established"),
    ({"status": "RECEIVED"}, "completed validated"),
    ({"status": "REJECTED"}, "completed validated"),
    ({"completed_at": None}, "completed validated"),
    ({"raw": b"altered"}, "digest or size mismatch"),
    ({"changes": {"byte_size": 1}}, "digest or size mismatch"),
    ({"changes": {"provider": "other"}}, "EGID security-master"),
    ({"changes": {"asset_type": "DAILY_BARS"}}, "EGID security-master"),
    ({"changes": {"symbol": "COMI"}}, "whole security-master"),
    ({"changes": {"granularity": "D1"}}, "whole security-master"),
    ({"raw": bytearray(RAW)}, "retained bytes required"),
    ({"completed_at": NOW.replace(tzinfo=None)}, "aware completion"),
    ({"as_of": NOW.replace(tzinfo=None)}, "aware as_of"),
    ({"completed_at": NOW - timedelta(seconds=1)}, "chronology"),
    ({"as_of": NOW - timedelta(seconds=1)}, "chronology"),
    ({"changes": {"market_date": date(2026, 11, 28)}}, "effective date after receipt"),
])
def test_refuses_unusable_retained_input(kwargs, match):
    with pytest.raises(ValueError, match=match):
        check(**kwargs)


@pytest.mark.parametrize("received_at,effective_date", [
    (NOW, date(2026, 11, 27)),
    # Cairo is already November 27 while the UTC receipt date is November 26.
    (datetime(2026, 11, 26, 23, tzinfo=timezone.utc), date(2026, 11, 27)),
])
def test_accepts_effective_date_on_cairo_receipt_day(received_at, effective_date):
    assert check(changes={"received_at": received_at, "market_date": effective_date}) == effective_date


def test_refuses_effective_date_before_cairo_day_has_started():
    with pytest.raises(ValueError, match="effective date after receipt"):
        check(changes={
            "received_at": datetime(2026, 11, 26, 21, 59, tzinfo=timezone.utc),
            "market_date": date(2026, 11, 27),
        })
