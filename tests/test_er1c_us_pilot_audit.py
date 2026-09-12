import json
import zlib
from datetime import date

import pytest

from tools.audit_er1c_us_pilot import (
    audit_rows,
    inspect_nyse_2022_calendar,
    inspect_twitter_corporate_event,
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


def calendar_pdf(*, text=None):
    content = text or "\n".join([
        "2022 TRADING CALENDAR",
        "Exchange Holiday  -  Market Closed",
        "Early Market Close at 1pm eastern",
        "Dates are correct as of Dec. 13 2021 but are subject to change.",
    ])
    operators = " ".join(f"({line}) Tj" for line in content.splitlines()).encode()
    stream = zlib.compress(b"BT " + operators + b" ET")
    return (
        b"%PDF-1.7\n<</Type/Pages/Count 1>>\n"
        b"<</CreationDate(D:20211213173946-05'00')>>\nstream\n"
        + stream
        + b"\nendstream\n%%EOF"
    )


def twitter_event_documents():
    filing = """<html><body>
    FORM 8-K Twitter, Inc. 0001418091
    Common Stock, par value $0.000005 per share TWTR New York Stock Exchange
    On October 27, 2022, pursuant to the terms of the Merger Agreement, the Merger was consummated.
    converted into the right to receive $54.20 in cash
    trading of Twitter’s common stock on the NYSE was suspended prior to the opening of the NYSE on October 28, 2022
    </body></html>""".encode()
    removal = """<DOCUMENT><TEXT>
    NOTIFICATION OF THE REMOVAL FROM LISTING AND REGISTRATION OF THE STATED SECURITIES
    The New York Stock Exchange hereby notifies the SEC
    opening of business on November 08, 2022
    The merger between Twitter, Inc. and X Holdings II, Inc. became effective on October 27, 2022
    Each share of Twitter, Inc. Common Stock was exchanged for USD 54.20 in cash
    suspended from trading before market open on October 28, 2022
    </TEXT></DOCUMENT>""".encode()
    return filing, removal


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


def test_nyse_calendar_is_scoped_as_corroboration_not_us2_session_evidence():
    result = inspect_nyse_2022_calendar(calendar_pdf())

    assert result["pdf_creation_date"] == "20211213173946-05'00'"
    assert result["states_dates_subject_to_change"] is True
    assert result["canonical_us2_session_evidence"] == "NO_GO"
    assert "exact UTC open and close clocks" in result["limitations"][-1]


def test_nyse_calendar_missing_scope_disclaimer_fails_closed():
    incomplete = calendar_pdf(text="2022 TRADING CALENDAR")

    with pytest.raises(ValueError, match="content anchors missing"):
        inspect_nyse_2022_calendar(incomplete)


def test_twitter_event_is_scoped_without_claiming_complete_action_coverage():
    result = inspect_twitter_corporate_event(*twitter_event_documents())

    assert result["cik"] == "0001418091"
    assert result["cash_consideration_usd_per_share"] == 54.20
    assert result["trading_suspended_before_open_date"] == "2022-10-28"
    assert result["canonical_us4_action_coverage"] == "NO_GO"


def test_twitter_event_missing_independent_cash_terms_fails_closed():
    filing, removal = twitter_event_documents()
    removal = removal.replace(b"USD 54.20", b"an unstated amount")

    with pytest.raises(ValueError, match="corporate-event anchors missing"):
        inspect_twitter_corporate_event(filing, removal)


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
