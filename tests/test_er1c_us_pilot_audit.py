import json
from datetime import date

import pytest

from tools.audit_er1c_us_pilot import (
    audit_rows,
    sha256_bytes,
    verify_predeclaration,
    verify_sha256_sidecar,
)


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


def test_sha256_sidecar_and_frozen_predeclaration_are_verified():
    content = b"frozen bytes\n"
    digest = sha256_bytes(content)

    assert verify_sha256_sidecar(
        content, f"{digest}  arbitrary-name\n".encode(), "artifact.sha256"
    ) == digest
    assert verify_predeclaration(content, digest) == digest


@pytest.mark.parametrize(
    "operation, message",
    [
        (
            lambda: verify_sha256_sidecar(
                b"actual", b"0" * 64 + b"  manifest.json\n", "manifest.sha256"
            ),
            "sidecar mismatch",
        ),
        (
            lambda: verify_predeclaration(b"changed", "0" * 64),
            "predeclaration SHA256 mismatch",
        ),
    ],
)
def test_chain_of_custody_mismatch_fails_closed(operation, message):
    with pytest.raises(ValueError, match=message):
        operation()


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
