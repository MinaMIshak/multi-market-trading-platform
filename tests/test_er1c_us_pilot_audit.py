import json
from datetime import date

import pytest

from tools.audit_er1c_us_pilot import (
    audit_rows,
    sha256_bytes,
    validate_acquisition_manifest,
    validate_evidence_manifest,
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


def acquisition_manifest():
    return {
        "predeclaration_commit": "a" * 40,
        "predeclaration_sha256": "b" * 64,
        "provider": "Tiingo",
        "purpose": "pilot",
        "requests": [{
            "completed_at_utc": "2026-09-12T15:22:14+00:00",
            "end_date": "2022-11-04",
            "endpoint": "https://api.tiingo.com/tiingo/daily/IBM/prices?startDate=2022-04-01&endDate=2022-11-04",
            "error": None,
            "http_status": 200,
            "provider": "Tiingo",
            "raw_byte_size": 1,
            "raw_file": "ibm.raw",
            "raw_sha256": "c" * 64,
            "safe_response_headers": {},
            "start_date": "2022-04-01",
            "started_at_utc": "2026-09-12T15:22:13+00:00",
            "ticker": "IBM",
        }],
        "schema_version": "er1c-tiingo-acquisition-v1",
        "token_persisted": False,
    }


def evidence_manifest():
    return {
        "records": [{
            "byte_size": 1,
            "error": None,
            "filename": "notice.html",
            "historical_availability_proven": False,
            "http_status": 200,
            "retrieval_completed_at_utc": "2026-09-12T15:32:47+00:00",
            "retrieval_started_at_utc": "2026-09-12T15:32:46+00:00",
            "sha256": "d" * 64,
            "source_url": "https://www.sec.gov/notice.html",
        }],
        "schema_version": "er1c-public-evidence-capture-v1",
    }


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


def test_manifest_metadata_and_request_identity_are_validated():
    manifest = acquisition_manifest()
    assert validate_acquisition_manifest(manifest) == manifest["requests"]
    evidence = evidence_manifest()
    assert validate_evidence_manifest(evidence) == evidence["records"]


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda value: value["requests"][0].update(raw_file="../outside.raw"), "unsafe raw filename"),
        (lambda value: value["requests"][0].update(completed_at_utc="2026-09-12T15:22:12+00:00"), "completion precedes start"),
        (lambda value: value["requests"][0].update(endpoint="https://api.tiingo.com/tiingo/daily/TWTR/prices?startDate=2022-04-01&endDate=2022-11-04"), "endpoint does not match"),
        (lambda value: value["requests"][0].update(http_status=500), "was not successful"),
    ],
)
def test_acquisition_manifest_rejects_unsafe_or_incoherent_metadata(change, message):
    manifest = acquisition_manifest()
    change(manifest)
    with pytest.raises(ValueError, match=message):
        validate_acquisition_manifest(manifest)


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda value: value["records"][0].update(filename="nested/notice.html"), "unsafe evidence filename"),
        (lambda value: value["records"][0].update(historical_availability_proven="false"), "claim must be boolean"),
        (lambda value: value["records"][0].update(source_url="http://www.sec.gov/notice.html"), "invalid evidence source URL"),
        (lambda value: value.update(schema_version="unknown"), "unsupported evidence manifest schema"),
    ],
)
def test_evidence_manifest_rejects_unsafe_or_ambiguous_metadata(change, message):
    manifest = evidence_manifest()
    change(manifest)
    with pytest.raises(ValueError, match=message):
        validate_evidence_manifest(manifest)


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
