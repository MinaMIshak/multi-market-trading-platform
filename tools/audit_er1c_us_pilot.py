"""Offline integrity and market-fact audit for an ER1C Tiingo bundle.

The audit deliberately stops short of canonical PIT admission.  It verifies the
captured bytes and reports source rows that require external market-state or
corporate-action evidence; it never infers those facts from vendor rows.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import zlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


RAW_FIELDS = {
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "divCash",
    "splitFactor",
}

MANIFEST_FIELDS = {
    "predeclaration_commit",
    "predeclaration_sha256",
    "provider",
    "purpose",
    "requests",
    "schema_version",
    "token_persisted",
}
REQUEST_FIELDS = {
    "completed_at_utc",
    "end_date",
    "endpoint",
    "error",
    "http_status",
    "provider",
    "raw_byte_size",
    "raw_file",
    "raw_sha256",
    "safe_response_headers",
    "start_date",
    "started_at_utc",
    "ticker",
}
EVIDENCE_MANIFEST_FIELDS = {"records", "schema_version"}
EVIDENCE_RECORD_FIELDS = {
    "byte_size",
    "error",
    "filename",
    "historical_availability_proven",
    "http_status",
    "retrieval_completed_at_utc",
    "retrieval_started_at_utc",
    "sha256",
    "source_url",
}

NYSE_CALENDAR_FILENAME = "nyse_2022_trading_calendar.pdf"
TWITTER_8K_FILENAME = "sec_twitter_merger_8k.html"
TWITTER_REMOVAL_FILENAME = "sec_nyse_twitter_removal_notice.html"
IBM_SUBMISSIONS_FILENAME = "sec_ibm_submissions.json"
IBM_2022Q2_10Q_FILENAME = "sec_ibm_2022q2_10q.html"
IBM_CASH_DIVIDEND_HISTORY_FILENAME = "ibm_cash_dividend_history.html"
IBM_CASH_DIVIDEND_HISTORY_URL = (
    "https://www.ibm.com/investor/governance/ibm-cash-dividends"
)
IBM_STOCK_SPLIT_HISTORY_FILENAME = "ibm_stock_split_history.html"
IBM_STOCK_SPLIT_HISTORY_URL = (
    "https://www.ibm.com/investor/help/ibm-stock-splits-and-ibm-stock-dividends"
)
IBM_DIVIDEND_NOTICE_SPECS = {
    "ibm_2022-04-26_dividend_notice.html": {
        "source_url": "https://newsroom.ibm.com/2022-04-26-IBM-BOARD-APPROVES-INCREASE-IN-QUARTERLY-CASH-DIVIDEND-FOR-THE-27th-CONSECUTIVE-YEAR",
        "announcement_date": "2022-04-26",
        "amount": "1.65",
        "record_date": "2022-05-10",
        "payable_date": "2022-06-10",
        "statement": "board of directors today declared an increase in the regular quarterly cash dividend to $1.65 per common share, payable June 10, 2022 to stockholders of record as of May 10, 2022",
    },
    "ibm_2022-07-25_dividend_notice.html": {
        "source_url": "https://newsroom.ibm.com/2022-07-25-IBM-BOARD-APPROVES-REGULAR-QUARTERLY-CASH-DIVIDEND",
        "announcement_date": "2022-07-25",
        "amount": "1.65",
        "record_date": "2022-08-10",
        "payable_date": "2022-09-10",
        "statement": "board of directors today declared a regular quarterly cash dividend of $1.65 per common share, payable September 10, 2022 to stockholders of record August 10, 2022",
    },
}
IBM_2022Q2_10Q_URL = (
    "https://www.sec.gov/Archives/edgar/data/51143/"
    "000155837022010985/ibm-20220630x10q.htm"
)
PILOT_TICKERS = {"IBM", "TWTR"}
NYSE_CALENDAR_TEXT_ANCHORS = {
    "2022 TRADING CALENDAR",
    "Exchange Holiday  -  Market Closed",
    "Early Market Close at 1pm eastern",
    "Dates are correct as of Dec. 13 2021 but are subject to change.",
}


def normalized_html_text(payload: bytes, label: str) -> str:
    """Extract stable visible text for tightly scoped retained HTML artifacts."""
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8 HTML") from error
    if not re.search(r"<(?:html|document)\b", decoded, re.IGNORECASE):
        raise ValueError(f"{label} is not HTML")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", decoded)).split())


def inspect_twitter_corporate_event(
    filing_payload: bytes, removal_payload: bytes
) -> dict[str, Any]:
    """Corroborate the retained TWTR merger/removal without claiming coverage."""
    filing = normalized_html_text(filing_payload, "Twitter 8-K")
    removal = normalized_html_text(removal_payload, "NYSE removal notice")
    filing_anchors = {
        "FORM 8-K",
        "Twitter, Inc.",
        "0001418091",
        "Common Stock, par value $0.000005 per share TWTR New York Stock Exchange",
        "On October 27, 2022, pursuant to the terms of the Merger Agreement, the Merger was consummated.",
        "converted into the right to receive $54.20 in cash",
        "trading of Twitter’s common stock on the NYSE was suspended prior to the opening of the NYSE on October 28, 2022",
    }
    removal_anchors = {
        "NOTIFICATION OF THE REMOVAL FROM LISTING AND REGISTRATION OF THE STATED SECURITIES",
        "The New York Stock Exchange hereby notifies the SEC",
        "opening of business on November 08, 2022",
        "The merger between Twitter, Inc. and X Holdings II, Inc.",
        "became effective on October 27, 2022",
        "Each share of Twitter, Inc. Common Stock was exchanged for USD 54.20 in cash",
        "suspended from trading before market open on October 28, 2022",
    }
    missing_filing = sorted(anchor for anchor in filing_anchors if anchor not in filing)
    missing_removal = sorted(anchor for anchor in removal_anchors if anchor not in removal)
    if missing_filing or missing_removal:
        raise ValueError(
            "Twitter corporate-event anchors missing: "
            f"filing={missing_filing}, removal_notice={missing_removal}"
        )
    return {
        "issuer": "Twitter, Inc.",
        "cik": "0001418091",
        "security": "Common Stock, par value $0.000005 per share",
        "symbol": "TWTR",
        "exchange": "New York Stock Exchange",
        "merger_effective_date": "2022-10-27",
        "cash_consideration_usd_per_share": 54.20,
        "trading_suspended_before_open_date": "2022-10-28",
        "formal_removal_opening_of_business_date": "2022-11-08",
        "independently_corroborated_by_retained_artifacts": True,
        "canonical_us4_action_coverage": "NO_GO",
        "limitations": [
            "artifacts prove this event but not complete action coverage for the acquisition interval",
            "artifact historical availability is not proven",
            "approved human review bindings are absent",
        ],
    }


def inspect_ibm_submission(payload: bytes) -> dict[str, Any]:
    """Corroborate IBM issuer metadata without projecting a dated identity."""
    try:
        submission = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("IBM SEC submission is not valid UTF-8 JSON") from error
    expected = {
        "cik": "0000051143",
        "name": "INTERNATIONAL BUSINESS MACHINES CORP",
        "ticker": "IBM",
        "exchange": "NYSE",
    }
    tickers = submission.get("tickers")
    exchanges = submission.get("exchanges")
    if (
        not isinstance(tickers, list)
        or not isinstance(exchanges, list)
        or len(tickers) != len(exchanges)
    ):
        raise ValueError("IBM SEC submission ticker/exchange arrays are absent")
    pairs = set(zip(tickers, exchanges, strict=False))
    actual = {"cik": submission.get("cik"), "name": submission.get("name")}
    if actual != {"cik": expected["cik"], "name": expected["name"]} or (
        expected["ticker"], expected["exchange"]
    ) not in pairs:
        raise ValueError("IBM SEC submission identity does not match frozen pilot")
    recent = submission.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])
    columns = (forms, filing_dates, accessions, primary_documents)
    target_filing = (
        "10-Q",
        "2022-07-25",
        "0001558370-22-010985",
        "ibm-20220630x10q.htm",
    )
    if len({len(column) for column in columns}) != 1 or target_filing not in zip(
        *columns, strict=True
    ):
        raise ValueError("IBM SEC submission lacks the target 2022 Q2 filing anchor")
    return {
        "issuer": expected["name"],
        "cik": expected["cik"],
        "symbol": expected["ticker"],
        "exchange": expected["exchange"],
        "current_submission_metadata_corroborated": True,
        "target_filing": {
            "form": target_filing[0],
            "filing_date": target_filing[1],
            "accession_number": target_filing[2],
            "primary_document": target_filing[3],
        },
        "canonical_us1_identity_evidence": "NO_GO",
        "limitations": [
            "the current SEC submission snapshot is not exact-date identity evidence",
            "SEC CIK is an issuer identifier, not a stable listing or instrument identifier",
            "artifact historical availability and approved human review bindings are absent",
        ],
    }


def inspect_ibm_2022q2_filing(payload: bytes) -> dict[str, Any]:
    """Corroborate one dated IBM listing statement without inferring continuity."""
    filing = normalized_html_text(payload, "IBM 2022 Q2 10-Q")
    anchors = {
        "FORM 10-Q",
        "FOR THE QUARTER ENDED JUNE 30, 2022",
        "INTERNATIONAL BUSINESS MACHINES CORPORATION",
        "Capital stock, par value $.20 per share IBM New York Stock Exchange",
        "0000051143",
    }
    missing = sorted(anchor for anchor in anchors if anchor not in filing)
    if missing:
        raise ValueError(f"IBM 2022 Q2 10-Q anchors missing: {missing}")
    return {
        "issuer": "INTERNATIONAL BUSINESS MACHINES CORPORATION",
        "cik": "0000051143",
        "form": "10-Q",
        "period_end": "2022-06-30",
        "filing_date_from_sec_submission": "2022-07-25",
        "security": "Capital stock, par value $.20 per share",
        "symbol": "IBM",
        "exchange": "New York Stock Exchange",
        "dated_listing_statement_corroborated": True,
        "canonical_us1_identity_evidence": "NO_GO",
        "limitations": [
            "one filing statement does not prove identity continuity across the pilot interval",
            "SEC CIK is an issuer identifier, not a stable listing or instrument identifier",
            "the filing does not itself evidence the canonical XNYS MIC mapping",
            "artifact historical availability and approved human review bindings are absent",
        ],
    }


def inspect_ibm_dividend_scope(payload: bytes) -> dict[str, Any]:
    """Qualify a reported dividend announcement, never infer its ex-date."""
    inspect_ibm_2022q2_filing(payload)
    filing = normalized_html_text(payload, "IBM dividend announcement")
    statement = (
        "On July 25, 2022 , the company announced that the Board of Directors "
        "approved a quarterly dividend of $ 1.65 per common share. The dividend "
        "is payable September 10, 2022 to shareholders of record on August 10, 2022 ."
    )
    if statement not in filing:
        raise ValueError("IBM dividend announcement terms missing or changed")
    return {
        "symbol": "IBM",
        "source_file": IBM_2022Q2_10Q_FILENAME,
        "source_locator": IBM_2022Q2_10Q_URL,
        "source_sha256": sha256_bytes(payload),
        "statement_locator": "page 52; paragraph beginning On July 25, 2022",
        "reported_announcement_date": "2022-07-25",
        "reported_amount_usd_per_common_share": "1.65",
        "reported_record_date": "2022-08-10",
        "reported_payable_date": "2022-09-10",
        "ex_date": None,
        "historical_available_at": None,
        "complete_bounded_action_coverage": False,
        "canonical_us4_action_coverage": "NO_GO",
        "limitations": [
            "reported announcement date is not proven historical availability",
            "record and payable dates do not establish an ex-date or actual payment",
            "matching vendor amount does not independently corroborate its ex-date",
            "one announcement does not establish complete bounded actions or empty coverage",
            "artifact historical availability and approved human review bindings are absent",
        ],
    }


def inspect_ibm_dividend_notice(
    payload: bytes, filename: str, source_url: str
) -> dict[str, Any]:
    """Qualify one retained issuer notice without deriving an ex-date or coverage."""
    if filename not in IBM_DIVIDEND_NOTICE_SPECS:
        raise ValueError(f"unexpected IBM dividend notice filename: {filename}")
    spec = IBM_DIVIDEND_NOTICE_SPECS[filename]
    if source_url != spec["source_url"]:
        raise ValueError(f"IBM dividend notice source locator mismatch: {filename}")
    notice = normalized_html_text(payload, f"IBM dividend notice {filename}")
    required = ["The IBM (NYSE: IBM )", spec["statement"]]
    if any(anchor not in notice for anchor in required):
        raise ValueError(f"IBM dividend notice terms missing or changed: {filename}")
    return {
        "symbol": "IBM",
        "source_file": filename,
        "source_locator": source_url,
        "source_sha256": sha256_bytes(payload),
        "statement_locator": f"press-release paragraph dated {spec['announcement_date']}",
        "reported_announcement_date": spec["announcement_date"],
        "reported_amount_usd_per_common_share": spec["amount"],
        "reported_record_date": spec["record_date"],
        "reported_payable_date": spec["payable_date"],
        "ex_date": None,
        "historical_available_at": None,
        "complete_bounded_action_coverage": False,
        "canonical_us4_action_coverage": "NO_GO",
        "limitations": [
            "current capture of a dated issuer page does not prove historical availability",
            "record and payable dates do not establish an ex-date or actual payment",
            "matching vendor amount does not independently corroborate its ex-date",
            "two issuer notices do not establish complete bounded actions or empty coverage",
            "approved human review bindings are absent",
        ],
    }


def inspect_ibm_cash_dividend_history(
    payload: bytes, source_url: str
) -> dict[str, Any]:
    """Qualify IBM's current payment history without inventing ex-dates."""
    if source_url != IBM_CASH_DIVIDEND_HISTORY_URL:
        raise ValueError("IBM cash-dividend history source locator mismatch")
    history = normalized_html_text(payload, "IBM cash-dividend history")
    anchors = {
        "Cash Dividends (2020 – present)",
        "The cash dividend rate per share is the actual amount paid per share.",
        "No adjustments were made for stock splits.",
        "430 USD 1.65 09/10/22 08/10/22",
        "429 USD 1.65 06/10/22 05/10/22",
    }
    missing = sorted(anchor for anchor in anchors if anchor not in history)
    if missing:
        raise ValueError(f"IBM cash-dividend history anchors missing: {missing}")
    # Text anchors alone cannot bind date roles: verify cells and their own
    # table's header, and reject duplicate/conflicting target distributions.
    expected_header = [
        "Dividend Number", "Actual amount per share", "Payable date", "Record date"
    ]
    expected_rows = {
        "429": ["429", "USD 1.65", "06/10/22", "05/10/22"],
        "430": ["430", "USD 1.65", "09/10/22", "08/10/22"],
    }
    seen: set[str] = set()
    for table in re.findall(rb"<table\b[^>]*>(.*?)</table\s*>", payload, re.I | re.S):
        rows = [
            [normalized_html_text(b"<html>" + cell + b"</html>", "IBM dividend cell") for cell in
             re.findall(rb"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", row, re.I | re.S)]
            for row in re.findall(rb"<tr\b[^>]*>(.*?)</tr\s*>", table, re.I | re.S)
        ]
        for row in rows:
            if not row or row[0] not in expected_rows:
                continue
            if rows[0] != expected_header:
                raise ValueError("IBM dividend table date-role headings missing or changed")
            if row[0] in seen or row != expected_rows[row[0]]:
                raise ValueError("IBM dividend table target row duplicated or changed")
            seen.add(row[0])
    if seen != set(expected_rows):
        raise ValueError("IBM dividend table target rows missing")
    return {
        "symbol": "IBM",
        "source_file": IBM_CASH_DIVIDEND_HISTORY_FILENAME,
        "source_locator": source_url,
        "payments": [
            {
                "dividend_number": 429,
                "actual_amount_usd_per_share": 1.65,
                "payable_date": "2022-06-10",
                "record_date": "2022-05-10",
            },
            {
                "dividend_number": 430,
                "actual_amount_usd_per_share": 1.65,
                "payable_date": "2022-09-10",
                "record_date": "2022-08-10",
            },
        ],
        "actual_payment_history_corroborated": True,
        "ex_dates_evidenced": False,
        "complete_bounded_action_coverage": False,
        "canonical_us4_action_coverage": "NO_GO",
        "limitations": [
            "the current page does not prove its content was historically available",
            "the page states record and payable dates but not ex-dates",
            "cash-dividend history does not cover every required corporate-action type",
            "approved human review bindings are absent",
        ],
    }


def inspect_ibm_stock_split_history(payload: bytes, source_url: str) -> dict[str, Any]:
    """Qualify IBM's issuer statement about its last split and stock dividend."""
    if source_url != IBM_STOCK_SPLIT_HISTORY_URL:
        raise ValueError("IBM stock-split history source locator mismatch")
    history = normalized_html_text(payload, "IBM stock-split history")
    statement = (
        "The last IBM stock split occurred in 1999 and the last stock dividend "
        "distribution occurred in 1967."
    )
    if statement not in history:
        raise ValueError("IBM stock-split history statement missing or changed")
    return {
        "symbol": "IBM",
        "source_file": IBM_STOCK_SPLIT_HISTORY_FILENAME,
        "source_locator": source_url,
        "last_stock_split_year": 1999,
        "last_stock_dividend_year": 1967,
        "no_split_or_stock_dividend_during_pilot_corroborated": True,
        "complete_bounded_action_coverage": False,
        "canonical_us4_action_coverage": "NO_GO",
        "limitations": [
            "the current page does not prove its content was historically available",
            "the issuer statement covers splits and stock dividends only",
            "approved human review bindings are absent",
        ],
    }


def inspect_pilot_identity_scope(
    acquisition_tickers: set[str],
    twitter_event_scope: dict[str, Any],
    ibm_submission_scope: dict[str, Any],
    ibm_filing_scope: dict[str, Any],
) -> dict[str, Any]:
    """Enforce the frozen cohort and state the retained US1 identity limit."""
    if acquisition_tickers != PILOT_TICKERS:
        raise ValueError(
            "acquisition cohort does not match frozen pilot: "
            f"expected={sorted(PILOT_TICKERS)}, actual={sorted(acquisition_tickers)}"
        )
    if twitter_event_scope.get("symbol") != "TWTR" or twitter_event_scope.get(
        "cik"
    ) != "0001418091":
        raise ValueError("Twitter filing identity does not match frozen pilot")
    if ibm_submission_scope.get("symbol") != "IBM" or ibm_submission_scope.get(
        "cik"
    ) != "0000051143":
        raise ValueError("IBM submission identity does not match frozen pilot")
    if ibm_filing_scope.get("symbol") != "IBM" or ibm_filing_scope.get(
        "cik"
    ) != "0000051143":
        raise ValueError("IBM filing identity does not match frozen pilot")

    return {
        "frozen_acquisition_symbols": sorted(acquisition_tickers),
        "twitter_filing_identity_corroborated": True,
        "twitter_cik": "0001418091",
        "twitter_symbol": "TWTR",
        "twitter_exchange_text": "New York Stock Exchange",
        "ibm_issuer_identity_artifact_retained": True,
        "ibm_current_submission_metadata_corroborated": True,
        "ibm_dated_listing_statement_corroborated": True,
        "stable_instrument_ids_evidenced": False,
        "exact_date_identity_interval_evidenced": False,
        "listing_mic_evidenced": False,
        "canonical_us1_identity_evidence": "NO_GO",
        "limitations": [
            "the retained Twitter filing corroborates one issuer, symbol, security, and exchange representation",
            "the retained IBM filing corroborates one dated listing statement, not interval continuity",
            "provider request symbols are mutable identifiers, not stable instrument IDs",
            "NYSE exchange text does not itself evidence the canonical XNYS MIC mapping",
            "no retained artifact proves exact-date identity on every required session date",
            "artifact historical availability and approved human review bindings are absent",
        ],
    }


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_sha256_sidecar(payload: bytes, sidecar: bytes, filename: str) -> str:
    """Verify a conventional SHA256 sidecar without trusting its label."""
    try:
        line = sidecar.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ValueError(f"invalid SHA256 sidecar: {filename}") from error
    fields = line.split()
    digest = sha256_bytes(payload)
    if len(fields) != 2 or fields[0] != digest:
        raise ValueError(f"SHA256 sidecar mismatch: {filename}")
    return digest


def verify_predeclaration(payload: bytes, expected_sha256: str) -> str:
    digest = sha256_bytes(payload)
    if digest != expected_sha256:
        raise ValueError("frozen predeclaration SHA256 mismatch")
    return digest


def require_exact_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{label} fields mismatch")


def safe_filename(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise ValueError(f"unsafe {label} filename")
    return value


def verify_closed_directory(
    directory: Path,
    expected_files: set[str],
    expected_directories: set[str],
    label: str,
) -> None:
    """Require a manifest-controlled directory with no undeclared artifacts."""
    expected = expected_files | expected_directories
    entries = {entry.name: entry for entry in directory.iterdir()}
    if set(entries) != expected:
        missing = sorted(expected.difference(entries))
        undeclared = sorted(set(entries).difference(expected))
        raise ValueError(
            f"{label} inventory mismatch: missing={missing}, undeclared={undeclared}"
        )
    for name in expected_files:
        entry = entries[name]
        if entry.is_symlink() or not entry.is_file():
            raise ValueError(f"{label} artifact is not a regular file: {name}")
    for name in expected_directories:
        entry = entries[name]
        if entry.is_symlink() or not entry.is_dir():
            raise ValueError(f"{label} entry is not a regular directory: {name}")


def utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"invalid {label} timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid {label} timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"non-UTC {label} timestamp")
    return parsed


def validate_acquisition_manifest(manifest: Any) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise ValueError("acquisition manifest must be an object")
    require_exact_fields(manifest, MANIFEST_FIELDS, "acquisition manifest")
    if manifest["schema_version"] != "er1c-tiingo-acquisition-v1":
        raise ValueError("unsupported acquisition manifest schema")
    if manifest["provider"] != "Tiingo" or manifest["token_persisted"] is not False:
        raise ValueError("invalid acquisition manifest provider or token policy")
    if not isinstance(manifest["requests"], list) or not manifest["requests"]:
        raise ValueError("acquisition manifest requests must be nonempty")

    tickers: set[str] = set()
    files: set[str] = set()
    for request in manifest["requests"]:
        if not isinstance(request, dict):
            raise ValueError("acquisition request must be an object")
        require_exact_fields(request, REQUEST_FIELDS, "acquisition request")
        ticker = request["ticker"]
        filename = safe_filename(request["raw_file"], "raw")
        if not isinstance(ticker, str) or not ticker or ticker in tickers:
            raise ValueError("invalid or duplicate acquisition ticker")
        if filename in files:
            raise ValueError("duplicate acquisition raw filename")
        tickers.add(ticker)
        files.add(filename)
        if request["provider"] != manifest["provider"]:
            raise ValueError("acquisition request provider mismatch")
        if request["http_status"] != 200 or request["error"] is not None:
            raise ValueError("acquisition request was not successful")
        if type(request["raw_byte_size"]) is not int or request["raw_byte_size"] <= 0:
            raise ValueError("invalid acquisition raw byte size")
        started = utc_timestamp(request["started_at_utc"], "acquisition start")
        completed = utc_timestamp(request["completed_at_utc"], "acquisition completion")
        if completed < started:
            raise ValueError("acquisition completion precedes start")
        start = date.fromisoformat(request["start_date"])
        end = date.fromisoformat(request["end_date"])
        if end < start:
            raise ValueError("acquisition end date precedes start date")
        endpoint = urlparse(request["endpoint"])
        query = parse_qs(endpoint.query, strict_parsing=True)
        if (
            endpoint.scheme != "https"
            or endpoint.hostname != "api.tiingo.com"
            or endpoint.path != f"/tiingo/daily/{ticker}/prices"
            or query != {"startDate": [request["start_date"]], "endDate": [request["end_date"]]}
        ):
            raise ValueError("acquisition endpoint does not match request identity")
    return manifest["requests"]


def validate_evidence_manifest(manifest: Any) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise ValueError("evidence manifest must be an object")
    require_exact_fields(manifest, EVIDENCE_MANIFEST_FIELDS, "evidence manifest")
    if manifest["schema_version"] != "er1c-public-evidence-capture-v1":
        raise ValueError("unsupported evidence manifest schema")
    if not isinstance(manifest["records"], list) or not manifest["records"]:
        raise ValueError("evidence records must be nonempty")
    filenames: set[str] = set()
    urls: set[str] = set()
    for record in manifest["records"]:
        if not isinstance(record, dict):
            raise ValueError("evidence record must be an object")
        require_exact_fields(record, EVIDENCE_RECORD_FIELDS, "evidence record")
        filename = safe_filename(record["filename"], "evidence")
        source_url = record["source_url"]
        if filename in filenames or source_url in urls:
            raise ValueError("duplicate evidence record identity")
        filenames.add(filename)
        urls.add(source_url)
        parsed_url = urlparse(source_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            raise ValueError("invalid evidence source URL")
        if record["http_status"] != 200 or record["error"] is not None:
            raise ValueError("evidence retrieval was not successful")
        if type(record["byte_size"]) is not int or record["byte_size"] <= 0:
            raise ValueError("invalid evidence byte size")
        if type(record["historical_availability_proven"]) is not bool:
            raise ValueError("historical availability claim must be boolean")
        started = utc_timestamp(record["retrieval_started_at_utc"], "evidence retrieval start")
        completed = utc_timestamp(record["retrieval_completed_at_utc"], "evidence retrieval completion")
        if completed < started:
            raise ValueError("evidence retrieval completion precedes start")
    return manifest["records"]


def inspect_nyse_2022_calendar(payload: bytes) -> dict[str, Any]:
    """Scope the retained NYSE calendar without treating it as US2 evidence."""
    if not payload.startswith(b"%PDF-"):
        raise ValueError("NYSE calendar is not a PDF")
    page_count = re.search(rb"/Type/Pages/Count\s+(\d+)", payload)
    creation = re.search(rb"/CreationDate\(D:(\d{14}[+-]\d{2}'\d{2}')\)", payload)
    if page_count is None or int(page_count.group(1)) != 1 or creation is None:
        raise ValueError("NYSE calendar PDF structure mismatch")

    text_fragments: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", payload, re.DOTALL):
        try:
            stream = zlib.decompress(match.group(1))
        except zlib.error:
            continue
        for block in re.findall(rb"BT(.*?)ET", stream, re.DOTALL):
            strings = re.findall(rb"\((?:\\.|[^\\)])*\)", block)
            if strings:
                text_fragments.append(
                    "".join(
                        re.sub(rb"\\([()\\])", rb"\1", value[1:-1]).decode(
                            "latin1", "replace"
                        )
                        for value in strings
                    )
                )
    extracted_text = "\n".join(text_fragments)
    missing = sorted(
        anchor for anchor in NYSE_CALENDAR_TEXT_ANCHORS if anchor not in extracted_text
    )
    if missing:
        raise ValueError(f"NYSE calendar content anchors missing: {missing}")

    return {
        "document_title": "2022 TRADING CALENDAR",
        "pdf_page_count": 1,
        "pdf_creation_date": creation.group(1).decode("ascii"),
        "states_exchange_holidays_are_closed": True,
        "states_early_close_is_1pm_eastern": True,
        "states_dates_subject_to_change": True,
        "canonical_us2_session_evidence": "NO_GO",
        "limitations": [
            "artifact says its dates are subject to change",
            "calendar does not bind each date to the declared XNYS MIC",
            "calendar does not state the regular session open",
            "calendar does not supply exact UTC open and close clocks per open date",
        ],
    }


def market_date(value: str) -> date:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.time().isoformat() != "00:00:00":
        raise ValueError(f"non-midnight Tiingo daily timestamp: {value}")
    return parsed.date()


def audit_rows(payload: bytes, start: date, end: date) -> dict[str, Any]:
    rows = json.loads(payload)
    if not isinstance(rows, list) or not rows:
        raise ValueError("raw response must be a nonempty JSON array")

    seen: set[date] = set()
    zero_volume: list[str] = []
    action_markers: list[dict[str, Any]] = []
    previous: date | None = None
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"row {number} must be an object")
        missing = RAW_FIELDS.difference(row)
        if missing:
            raise ValueError(f"row {number} missing fields: {sorted(missing)}")
        day = market_date(row["date"])
        if day < start or day > end:
            raise ValueError(f"row {number} outside declared request bounds")
        if day in seen or (previous is not None and day <= previous):
            raise ValueError(f"duplicate or unordered market date at row {number}")
        seen.add(day)
        previous = day

        prices = tuple(row[field] for field in ("open", "high", "low", "close"))
        if any(
            type(value) not in (int, float)
            or (isinstance(value, float) and not math.isfinite(value))
            or value <= 0
            for value in prices
        ):
            raise ValueError(
                f"row {number} has nonpositive raw price or non-finite/nonnumeric value"
            )
        if row["low"] > min(row["open"], row["close"], row["high"]):
            raise ValueError(f"row {number} has incoherent raw low")
        if row["high"] < max(row["open"], row["close"], row["low"]):
            raise ValueError(f"row {number} has incoherent raw high")
        if (
            type(row["volume"]) not in (int, float)
            or (isinstance(row["volume"], float) and not math.isfinite(row["volume"]))
            or row["volume"] < 0
        ):
            raise ValueError(f"row {number} has invalid volume")
        for field in ("divCash", "splitFactor"):
            value = row[field]
            if (
                type(value) not in (int, float)
                or (isinstance(value, float) and not math.isfinite(value))
                or value < 0
                or (field == "splitFactor" and value == 0)
            ):
                raise ValueError(f"row {number} has invalid {field}")
        if row["volume"] == 0:
            zero_volume.append(day.isoformat())
        if row["divCash"] != 0 or row["splitFactor"] != 1:
            action_markers.append(
                {
                    "date": day.isoformat(),
                    "divCash": row["divCash"],
                    "splitFactor": row["splitFactor"],
                    "row_number": number,
                }
            )

    return {
        "row_count": len(rows),
        "first_market_date": min(seen).isoformat(),
        "last_market_date": max(seen).isoformat(),
        "zero_volume_dates_requiring_external_classification": zero_volume,
        "vendor_action_markers_not_action_coverage": action_markers,
    }


def audit_bundle(bundle: Path, predeclaration: Path) -> dict[str, Any]:
    manifest_payload = (bundle / "manifest.json").read_bytes()
    manifest_sha256 = verify_sha256_sidecar(
        manifest_payload,
        (bundle / "manifest.sha256").read_bytes(),
        "manifest.sha256",
    )
    manifest = json.loads(manifest_payload)
    requests = validate_acquisition_manifest(manifest)
    verify_closed_directory(
        bundle,
        {"manifest.json", "manifest.sha256"}
        | {request["raw_file"] for request in requests},
        {"evidence"},
        "acquisition bundle",
    )
    predeclaration_sha256 = verify_predeclaration(
        predeclaration.read_bytes(), manifest["predeclaration_sha256"]
    )
    results: dict[str, Any] = {}
    for request in requests:
        raw_path = bundle / request["raw_file"]
        payload = raw_path.read_bytes()
        if len(payload) != request["raw_byte_size"]:
            raise ValueError(f"byte-size mismatch: {raw_path.name}")
        if sha256_bytes(payload) != request["raw_sha256"]:
            raise ValueError(f"SHA256 mismatch: {raw_path.name}")
        results[request["ticker"]] = {
            "raw_file": raw_path.name,
            "raw_byte_size": len(payload),
            "raw_sha256": sha256_bytes(payload),
            **audit_rows(
                payload,
                date.fromisoformat(request["start_date"]),
                date.fromisoformat(request["end_date"]),
            ),
        }

    evidence_manifest_path = bundle / "evidence" / "evidence_manifest.json"
    evidence_manifest_payload = evidence_manifest_path.read_bytes()
    evidence_manifest_sha256 = verify_sha256_sidecar(
        evidence_manifest_payload,
        (evidence_manifest_path.parent / "evidence_manifest.sha256").read_bytes(),
        "evidence_manifest.sha256",
    )
    evidence_manifest = json.loads(evidence_manifest_payload)
    evidence_records = validate_evidence_manifest(evidence_manifest)
    verify_closed_directory(
        evidence_manifest_path.parent,
        {"evidence_manifest.json", "evidence_manifest.sha256"}
        | {record["filename"] for record in evidence_records},
        set(),
        "public evidence",
    )
    evidence_results = []
    calendar_scope = None
    evidence_payloads: dict[str, bytes] = {}
    evidence_source_urls: dict[str, str] = {}
    for record in evidence_records:
        payload = (evidence_manifest_path.parent / record["filename"]).read_bytes()
        if len(payload) != record["byte_size"]:
            raise ValueError(f"evidence byte-size mismatch: {record['filename']}")
        if sha256_bytes(payload) != record["sha256"]:
            raise ValueError(f"evidence SHA256 mismatch: {record['filename']}")
        evidence_results.append(
            {
                "filename": record["filename"],
                "byte_size": len(payload),
                "sha256": sha256_bytes(payload),
                "historical_availability_proven": record[
                    "historical_availability_proven"
                ],
            }
        )
        evidence_payloads[record["filename"]] = payload
        evidence_source_urls[record["filename"]] = record["source_url"]
        if record["filename"] == NYSE_CALENDAR_FILENAME:
            calendar_scope = inspect_nyse_2022_calendar(payload)

    if calendar_scope is None:
        raise ValueError("retained NYSE 2022 calendar evidence is absent")
    missing_twitter = sorted(
        {TWITTER_8K_FILENAME, TWITTER_REMOVAL_FILENAME}.difference(evidence_payloads)
    )
    if missing_twitter:
        raise ValueError(
            f"retained Twitter corporate-event evidence is absent: {missing_twitter}"
        )
    twitter_event_scope = inspect_twitter_corporate_event(
        evidence_payloads[TWITTER_8K_FILENAME],
        evidence_payloads[TWITTER_REMOVAL_FILENAME],
    )
    if IBM_SUBMISSIONS_FILENAME not in evidence_payloads:
        raise ValueError("retained IBM SEC submission evidence is absent")
    ibm_submission_scope = inspect_ibm_submission(
        evidence_payloads[IBM_SUBMISSIONS_FILENAME]
    )
    if IBM_2022Q2_10Q_FILENAME not in evidence_payloads:
        raise ValueError("retained IBM 2022 Q2 filing evidence is absent")
    if evidence_source_urls[IBM_2022Q2_10Q_FILENAME] != IBM_2022Q2_10Q_URL:
        raise ValueError("IBM 2022 Q2 filing source locator mismatch")
    ibm_filing_scope = inspect_ibm_2022q2_filing(
        evidence_payloads[IBM_2022Q2_10Q_FILENAME]
    )
    identity_scope = inspect_pilot_identity_scope(
        set(results), twitter_event_scope, ibm_submission_scope, ibm_filing_scope
    )
    missing_notices = sorted(set(IBM_DIVIDEND_NOTICE_SPECS).difference(evidence_payloads))
    if missing_notices:
        raise ValueError(f"retained IBM dividend notices are absent: {missing_notices}")
    dividend_notices = [
        inspect_ibm_dividend_notice(
            evidence_payloads[filename], filename, evidence_source_urls[filename]
        )
        for filename in sorted(IBM_DIVIDEND_NOTICE_SPECS)
    ]
    if IBM_CASH_DIVIDEND_HISTORY_FILENAME not in evidence_payloads:
        raise ValueError("retained IBM cash-dividend history is absent")
    cash_dividend_history = inspect_ibm_cash_dividend_history(
        evidence_payloads[IBM_CASH_DIVIDEND_HISTORY_FILENAME],
        evidence_source_urls[IBM_CASH_DIVIDEND_HISTORY_FILENAME],
    )
    if IBM_STOCK_SPLIT_HISTORY_FILENAME not in evidence_payloads:
        raise ValueError("retained IBM stock-split history is absent")
    stock_split_history = inspect_ibm_stock_split_history(
        evidence_payloads[IBM_STOCK_SPLIT_HISTORY_FILENAME],
        evidence_source_urls[IBM_STOCK_SPLIT_HISTORY_FILENAME],
    )

    return {
        "schema_version": "er1c-offline-audit-v1",
        "bundle": str(bundle),
        "manifest_sha256": manifest_sha256,
        "predeclaration_commit": manifest["predeclaration_commit"],
        "predeclaration_sha256": predeclaration_sha256,
        "frozen_predeclaration_verified": True,
        "prices": results,
        "public_evidence_manifest_sha256": evidence_manifest_sha256,
        "public_evidence": evidence_results,
        "nyse_2022_calendar_scope": calendar_scope,
        "twitter_corporate_event_scope": twitter_event_scope,
        "ibm_submission_scope": ibm_submission_scope,
        "ibm_2022q2_filing_scope": ibm_filing_scope,
        "ibm_dividend_scope": inspect_ibm_dividend_scope(
            evidence_payloads[IBM_2022Q2_10Q_FILENAME]
        ),
        "ibm_dividend_notice_scopes": dividend_notices,
        "ibm_cash_dividend_history_scope": cash_dividend_history,
        "ibm_stock_split_history_scope": stock_split_history,
        "pilot_identity_scope": identity_scope,
        "canonical_pit_admission": "NO_GO",
        "admission_blockers": [
            "source historical availability is not proven",
            "approved human review bindings are absent",
            "complete exact-date XNYS universe evidence is absent",
            "exact-date identity evidence across the interval is absent",
            "complete bounded corporate-action coverage is absent",
            "every-calendar-date evidenced XNYS session records are absent",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument(
        "--predeclaration",
        type=Path,
        default=Path("docs/ER1C_US_PILOT_PREDECLARATION.md"),
        help="frozen declaration whose bytes must match the acquisition manifest",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            audit_bundle(args.bundle, args.predeclaration), indent=2, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
