import json
import zlib
from datetime import date

import pytest

from tools.audit_er1c_us_pilot import (
    audit_rows,
    inspect_nyse_2022_calendar,
    inspect_ibm_submission,
    inspect_ibm_dividend_scope,
    inspect_ibm_dividend_notice,
    inspect_ibm_cash_dividend_history,
    inspect_ibm_stock_split_history,
    inspect_ibm_2022q2_filing,
    inspect_pilot_identity_scope,
    inspect_twitter_corporate_event,
    sha256_bytes,
    validate_acquisition_manifest,
    validate_evidence_manifest,
    verify_closed_directory,
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
            "safe_response_headers": {
                "content-length": "1",
                "content-type": "application/json",
                "date": "Sat, 12 Sep 2026 15:22:13 GMT",
            },
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


def ibm_submission():
    return json.dumps({
        "cik": "0000051143",
        "name": "INTERNATIONAL BUSINESS MACHINES CORP",
        "tickers": ["IBM"],
        "exchanges": ["NYSE"],
        "filings": {"recent": {
            "form": ["10-Q"],
            "filingDate": ["2022-07-25"],
            "accessionNumber": ["0001558370-22-010985"],
            "primaryDocument": ["ibm-20220630x10q.htm"],
        }},
    }).encode()


def ibm_2022q2_filing():
    return """<html><body>
    FORM 10-Q FOR THE QUARTER ENDED JUNE 30, 2022
    INTERNATIONAL BUSINESS MACHINES CORPORATION 0000051143
    Capital stock, par value $.20 per share IBM New York Stock Exchange
    </body></html>""".encode()


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


def test_pilot_identity_scope_keeps_filing_corroboration_out_of_us1():
    event = inspect_twitter_corporate_event(*twitter_event_documents())
    ibm = inspect_ibm_submission(ibm_submission())

    result = inspect_pilot_identity_scope(
        {"TWTR", "IBM"}, event, ibm, inspect_ibm_2022q2_filing(ibm_2022q2_filing())
    )

    assert result["twitter_filing_identity_corroborated"] is True
    assert result["ibm_issuer_identity_artifact_retained"] is True
    assert result["ibm_dated_listing_statement_corroborated"] is True
    assert result["stable_instrument_ids_evidenced"] is False
    assert result["exact_date_identity_interval_evidenced"] is False
    assert result["canonical_us1_identity_evidence"] == "NO_GO"


def test_pilot_identity_scope_rejects_acquisition_cohort_drift():
    event = inspect_twitter_corporate_event(*twitter_event_documents())
    ibm = inspect_ibm_submission(ibm_submission())

    with pytest.raises(ValueError, match="does not match frozen pilot"):
        inspect_pilot_identity_scope(
            {"IBM"}, event, ibm, inspect_ibm_2022q2_filing(ibm_2022q2_filing())
        )


def test_ibm_2022q2_filing_is_dated_corroboration_not_interval_us1_evidence():
    result = inspect_ibm_2022q2_filing(ibm_2022q2_filing())

    assert result["period_end"] == "2022-06-30"
    assert result["filing_date_from_sec_submission"] == "2022-07-25"
    assert result["dated_listing_statement_corroborated"] is True
    assert result["canonical_us1_identity_evidence"] == "NO_GO"


def test_ibm_2022q2_filing_rejects_missing_listing_anchor():
    incomplete = ibm_2022q2_filing().replace(
        b"New York Stock Exchange", b"an unspecified exchange"
    )

    with pytest.raises(ValueError, match="10-Q anchors missing"):
        inspect_ibm_2022q2_filing(incomplete)


def test_ibm_submission_is_current_corroboration_not_dated_us1_evidence():
    result = inspect_ibm_submission(ibm_submission())

    assert result["cik"] == "0000051143"
    assert result["current_submission_metadata_corroborated"] is True
    assert result["canonical_us1_identity_evidence"] == "NO_GO"


def test_ibm_submission_rejects_wrong_identity_or_missing_2022_anchor():
    wrong = json.loads(ibm_submission())
    wrong["tickers"] = ["OTHER"]
    with pytest.raises(ValueError, match="does not match frozen pilot"):
        inspect_ibm_submission(json.dumps(wrong).encode())

    missing = json.loads(ibm_submission())
    missing["filings"]["recent"]["accessionNumber"] = ["other-accession"]
    with pytest.raises(ValueError, match="lacks the target 2022 Q2 filing anchor"):
        inspect_ibm_submission(json.dumps(missing).encode())

    unpaired = json.loads(ibm_submission())
    unpaired["exchanges"] = []
    with pytest.raises(ValueError, match="ticker/exchange arrays are absent"):
        inspect_ibm_submission(json.dumps(unpaired).encode())


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda value: value["requests"][0].update(raw_file="../outside.raw"), "unsafe raw filename"),
        (lambda value: value["requests"][0].update(completed_at_utc="2026-09-12T15:22:12+00:00"), "completion precedes start"),
        (lambda value: value["requests"][0].update(endpoint="https://api.tiingo.com/tiingo/daily/TWTR/prices?startDate=2022-04-01&endDate=2022-11-04"), "endpoint does not match"),
        (lambda value: value["requests"][0].update(http_status=500), "was not successful"),
        (lambda value: value["requests"][0]["safe_response_headers"].update({"authorization": "secret"}), "headers are incomplete or unexpected"),
        (lambda value: value["requests"][0]["safe_response_headers"].update({"content-length": "2"}), "content length mismatch"),
        (lambda value: value["requests"][0]["safe_response_headers"].update({"content-type": "text/html"}), "content type is not JSON"),
        (lambda value: value["requests"][0]["safe_response_headers"].update({"date": "Sat, 12 Sep 2026 15:23:13 GMT"}), "outside request interval"),
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


def test_closed_directory_accepts_exact_regular_inventory(tmp_path):
    (tmp_path / "manifest.json").write_bytes(b"{}")
    (tmp_path / "raw.json").write_bytes(b"[]")
    (tmp_path / "evidence").mkdir()

    verify_closed_directory(
        tmp_path, {"manifest.json", "raw.json"}, {"evidence"}, "bundle"
    )


@pytest.mark.parametrize(
    "mutation,message",
    [
        (lambda path: (path / "undeclared.html").write_bytes(b"x"), "undeclared"),
        (lambda path: (path / "raw.json").unlink(), "missing"),
    ],
)
def test_closed_directory_rejects_incomplete_or_undeclared_inventory(
    tmp_path, mutation, message
):
    (tmp_path / "raw.json").write_bytes(b"[]")
    mutation(tmp_path)

    with pytest.raises(ValueError, match=message):
        verify_closed_directory(tmp_path, {"raw.json"}, set(), "evidence")


def test_closed_directory_rejects_symlinked_artifact(tmp_path):
    target = tmp_path.parent / "outside.raw"
    target.write_bytes(b"[]")
    (tmp_path / "raw.json").symlink_to(target)

    with pytest.raises(ValueError, match="not a regular file"):
        verify_closed_directory(tmp_path, {"raw.json"}, set(), "evidence")


def test_closed_directory_rejects_symlinked_root(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "raw.json").write_bytes(b"[]")
    linked_package = tmp_path / "linked-package"
    linked_package.symlink_to(package, target_is_directory=True)

    with pytest.raises(ValueError, match="must be a real directory"):
        verify_closed_directory(linked_package, {"raw.json"}, set(), "evidence")


def test_closed_directory_rejects_hard_linked_artifact(tmp_path):
    outside = tmp_path / "outside.raw"
    outside.write_bytes(b"[]")
    package = tmp_path / "package"
    package.mkdir()
    (package / "raw.json").hardlink_to(outside)

    with pytest.raises(ValueError, match="must not be hard linked"):
        verify_closed_directory(package, {"raw.json"}, set(), "evidence")


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


@pytest.mark.parametrize(
    "timestamp, message",
    [
        ("2022-10-27T00:00:00", "non-UTC"),
        ("2022-10-27T00:00:00+02:00", "non-UTC"),
        ("2022-10-27T00:00:01Z", "non-midnight"),
        (None, "invalid Tiingo daily timestamp"),
    ],
)
def test_audit_requires_explicit_utc_midnight_daily_timestamps(timestamp, message):
    with pytest.raises(ValueError, match=message):
        audit_rows(payload(row(day=timestamp)), date(2022, 4, 1), date(2022, 11, 4))


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume", "divCash", "splitFactor"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, None, "1"])
def test_audit_rejects_nonfinite_or_nonnumeric_market_values(field, value):
    with pytest.raises(ValueError, match="raw price|invalid"):
        audit_rows(payload(row(**{field: value})), date(2022, 4, 1), date(2022, 11, 4))


@pytest.mark.parametrize("changes", [{"volume": -1}, {"divCash": -1}, {"splitFactor": -1}, {"splitFactor": 0}])
def test_audit_rejects_invalid_volume_and_action_domains(changes):
    with pytest.raises(ValueError, match="invalid"):
        audit_rows(payload(row(**changes)), date(2022, 4, 1), date(2022, 11, 4))


@pytest.mark.parametrize("value", [None, [], 1, "date"])
def test_audit_rejects_nonobject_rows(value):
    with pytest.raises(ValueError, match="must be an object"):
        audit_rows(payload(value), date(2022, 4, 1), date(2022, 11, 4))


def test_audit_preserves_reverse_split_marker_without_deriving_coverage():
    result = audit_rows(payload(row(splitFactor=0.1)), date(2022, 4, 1), date(2022, 11, 4))
    assert result["vendor_action_markers_not_action_coverage"][0]["splitFactor"] == 0.1


def ibm_dividend_filing():
    # Software fixture only; not empirical evidence.
    return ibm_2022q2_filing().replace(b"</body>", (
        b"On July 25, 2022 , the company announced that the Board of Directors "
        b"approved a quarterly dividend of $ 1.65 per common share. The dividend "
        b"is payable September 10, 2022 to shareholders of record on August 10, 2022 ."
        b"</body>"
    ))


def test_ibm_dividend_preserves_date_roles_without_admitting_actions():
    source = ibm_dividend_filing()
    result = inspect_ibm_dividend_scope(source)
    assert result["source_sha256"] == sha256_bytes(source)
    assert result["reported_announcement_date"] == "2022-07-25"
    assert result["reported_record_date"] == "2022-08-10"
    assert result["reported_payable_date"] == "2022-09-10"
    assert result["reported_amount_usd_per_common_share"] == "1.65"
    assert result["ex_date"] is None
    assert result["historical_available_at"] is None
    assert result["complete_bounded_action_coverage"] is False
    assert result["canonical_us4_action_coverage"] == "NO_GO"


@pytest.mark.parametrize("old,new", [
    (b"July 25", b"July 26"),
    (b"1.65", b"1.66"),
    (b"September 10", b"September 11"),
    (b"August 10", b"August 09"),
    (b"per common share", b"per preferred share"),
    (b"0000051143", b"0001418091"),
])
def test_ibm_dividend_rejects_changed_terms_or_issuer(old, new):
    with pytest.raises(ValueError):
        inspect_ibm_dividend_scope(ibm_dividend_filing().replace(old, new))


def test_ibm_listing_statement_alone_cannot_prove_dividend():
    with pytest.raises(ValueError, match="dividend announcement terms"):
        inspect_ibm_dividend_scope(ibm_2022q2_filing())


@pytest.mark.parametrize(
    "filename,url,announcement,record,payable,statement",
    [
        (
            "ibm_2022-04-26_dividend_notice.html",
            "https://newsroom.ibm.com/2022-04-26-IBM-BOARD-APPROVES-INCREASE-IN-QUARTERLY-CASH-DIVIDEND-FOR-THE-27th-CONSECUTIVE-YEAR",
            "2022-04-26", "2022-05-10", "2022-06-10",
            "board of directors today declared an increase in the regular quarterly cash dividend to $1.65 per common share, payable June 10, 2022 to stockholders of record as of May 10, 2022",
        ),
        (
            "ibm_2022-07-25_dividend_notice.html",
            "https://newsroom.ibm.com/2022-07-25-IBM-BOARD-APPROVES-REGULAR-QUARTERLY-CASH-DIVIDEND",
            "2022-07-25", "2022-08-10", "2022-09-10",
            "board of directors today declared a regular quarterly cash dividend of $1.65 per common share, payable September 10, 2022 to stockholders of record August 10, 2022",
        ),
    ],
)
def test_ibm_dividend_notice_preserves_date_roles(
    filename, url, announcement, record, payable, statement
):
    payload = f"<html><body>The IBM (NYSE: IBM ) {statement}</body></html>".encode()
    result = inspect_ibm_dividend_notice(payload, filename, url)
    assert result["reported_announcement_date"] == announcement
    assert result["reported_record_date"] == record
    assert result["reported_payable_date"] == payable
    assert result["ex_date"] is None
    assert result["historical_available_at"] is None
    assert result["complete_bounded_action_coverage"] is False
    assert result["canonical_us4_action_coverage"] == "NO_GO"


def test_ibm_dividend_notice_rejects_locator_or_changed_terms():
    filename = "ibm_2022-07-25_dividend_notice.html"
    url = "https://newsroom.ibm.com/2022-07-25-IBM-BOARD-APPROVES-REGULAR-QUARTERLY-CASH-DIVIDEND"
    statement = "board of directors today declared a regular quarterly cash dividend of $1.65 per common share, payable September 10, 2022 to stockholders of record August 10, 2022"
    payload = f"<html>The IBM (NYSE: IBM ) {statement}</html>".encode()
    with pytest.raises(ValueError, match="source locator mismatch"):
        inspect_ibm_dividend_notice(payload, filename, url + "?changed=1")
    with pytest.raises(ValueError, match="terms missing or changed"):
        inspect_ibm_dividend_notice(
            payload.replace(b"$1.65", b"$1.66"), filename, url
        )


def ibm_cash_dividend_history():
    return """<html><body>
    Cash Dividends (2020 – present)
    The cash dividend rate per share is the actual amount paid per share.
    No adjustments were made for stock splits.
    <table><tr><th>Dividend Number</th><th>Actual amount per share</th>
    <th>Payable date</th><th>Record date</th></tr>
    <tr><td>430</td><td>USD 1.65</td><td>09/10/22</td><td>08/10/22</td></tr>
    <tr><td>429</td><td>USD 1.65</td><td>06/10/22</td><td>05/10/22</td></tr>
    </table>
    </body></html>""".encode()


def ibm_stock_split_history():
    return """<html><body>
    The last IBM stock split occurred in 1999 and the last stock dividend
    distribution occurred in 1967.
    </body></html>""".encode()


def test_ibm_payment_history_confirms_payments_but_not_ex_dates_or_us4():
    result = inspect_ibm_cash_dividend_history(
        ibm_cash_dividend_history(),
        "https://www.ibm.com/investor/governance/ibm-cash-dividends",
    )

    assert [payment["dividend_number"] for payment in result["payments"]] == [429, 430]
    assert result["actual_payment_history_corroborated"] is True
    assert result["ex_dates_evidenced"] is False
    assert result["canonical_us4_action_coverage"] == "NO_GO"


@pytest.mark.parametrize(
    "payload_value, url, message",
    [
        (
            ibm_cash_dividend_history().replace(b"429</td><td>USD 1.65", b"429</td><td>USD 9.99"),
            "https://www.ibm.com/investor/governance/ibm-cash-dividends",
            "anchors missing",
        ),
        (
            ibm_cash_dividend_history(),
            "https://www.ibm.com/investor/governance/changed",
            "source locator mismatch",
        ),
    ],
)
def test_ibm_payment_history_rejects_changed_terms_or_locator(
    payload_value, url, message
):
    with pytest.raises(ValueError, match=message):
        inspect_ibm_cash_dividend_history(payload_value, url)


def test_ibm_split_history_scopes_negative_evidence_to_two_action_types():
    result = inspect_ibm_stock_split_history(
        ibm_stock_split_history(),
        "https://www.ibm.com/investor/help/ibm-stock-splits-and-ibm-stock-dividends",
    )

    assert result["last_stock_split_year"] == 1999
    assert result["last_stock_dividend_year"] == 1967
    assert result["no_split_or_stock_dividend_during_pilot_corroborated"] is True
    assert result["canonical_us4_action_coverage"] == "NO_GO"


def test_ibm_split_history_rejects_changed_statement_or_locator():
    url = "https://www.ibm.com/investor/help/ibm-stock-splits-and-ibm-stock-dividends"
    with pytest.raises(ValueError, match="statement missing"):
        inspect_ibm_stock_split_history(
            ibm_stock_split_history().replace(b"1999", b"2000"), url
        )
    with pytest.raises(ValueError, match="source locator mismatch"):
        inspect_ibm_stock_split_history(ibm_stock_split_history(), url + "?changed=1")


@pytest.mark.parametrize("mutation", ["swapped_headings", "missing_heading", "duplicate", "conflict", "outside_table", "split_tables"])
def test_ibm_payment_history_requires_unambiguous_table_date_roles(mutation):
    source = ibm_cash_dividend_history()
    target = b"<tr><td>429</td><td>USD 1.65</td><td>06/10/22</td><td>05/10/22</td></tr>"
    if mutation == "swapped_headings":
        source = source.replace(b"Payable date", b"TEMP").replace(b"Record date", b"Payable date").replace(b"TEMP", b"Record date")
    elif mutation == "missing_heading":
        source = source.replace(b"Payable date", b"Date")
    elif mutation == "duplicate":
        source = source.replace(b"</table>", target + b"</table>")
    elif mutation == "conflict":
        source = source.replace(b"</table>", target.replace(b"06/10/22", b"06/11/22") + b"</table>")
    elif mutation == "outside_table":
        source = source.replace(target, b"") + target
    else:
        source = source.replace(target, b"</table><table>" + target)
    with pytest.raises(ValueError, match="IBM dividend table"):
        inspect_ibm_cash_dividend_history(source, "https://www.ibm.com/investor/governance/ibm-cash-dividends")
