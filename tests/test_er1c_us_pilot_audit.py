import json
from datetime import date

import pytest

from tools.audit_er1c_us_pilot import audit_rows


def payload(*rows):
    return json.dumps(rows, separators=(",", ":")).encode()


def row(day="2022-10-27T00:00:00.000Z", volume=10, **changes):
    value = {
        "date": day,
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": volume,
        "divCash": 0.0,
        "splitFactor": 1.0,
    }
    value.update(changes)
    return value


def test_audit_reports_zero_volume_without_interpreting_market_state():
    result = audit_rows(
        payload(row(volume=0)), date(2022, 4, 1), date(2022, 11, 4)
    )

    assert result["zero_volume_dates_requiring_external_classification"] == [
        "2022-10-27"
    ]
    assert "session" not in result


def test_audit_labels_vendor_action_fields_as_markers_not_coverage():
    result = audit_rows(
        payload(row(divCash=1.65)), date(2022, 4, 1), date(2022, 11, 4)
    )

    assert result["vendor_action_markers_not_action_coverage"] == [
        {
            "date": "2022-10-27",
            "divCash": 1.65,
            "splitFactor": 1.0,
            "row_number": 1,
        }
    ]


@pytest.mark.parametrize(
    "rows, message",
    [
        ((row(), row()), "duplicate or unordered"),
        ((row(low=10.75),), "incoherent raw low"),
        ((row(day="2022-11-05T00:00:00.000Z"),), "outside declared"),
    ],
)
def test_audit_rejects_noncanonical_price_rows(rows, message):
    with pytest.raises(ValueError, match=message):
        audit_rows(payload(*rows), date(2022, 4, 1), date(2022, 11, 4))
