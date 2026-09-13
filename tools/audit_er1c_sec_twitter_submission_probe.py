"""Offline audit for the bounded ER1C Twitter SEC submission probe."""
from __future__ import annotations
import argparse, hashlib, html, json, re
from pathlib import Path
from datetime import datetime, timezone

EXPECTED_SOURCES = {
    "twitter_20220418_8a12b_submission.txt": "https://www.sec.gov/Archives/edgar/data/1418091/000119312522107480/0001193125-22-107480.txt?output=1",
    "twitter_20221028_25nse_submission.txt": "https://www.sec.gov/Archives/edgar/data/1418091/000087666122000890/0000876661-22-000890.txt?output=1",
}
EXPECTED_FILES = set(EXPECTED_SOURCES)
SUBMISSION_IDENTITIES = {
    "twitter_20220418_8a12b_submission.txt": {
        "accession": "0001193125-22-107480", "acceptance_datetime": "20220418093659",
        "submission_type": "8-A12B", "public_document_count": "1", "file_number": "001-36164",
    },
    "twitter_20221028_25nse_submission.txt": {
        "accession": "0000876661-22-000890", "acceptance_datetime": "20221028083119",
        "submission_type": "25-NSE", "public_document_count": "2", "file_number": "001-36164",
    },
}

def _validate_provenance(record: dict) -> None:
    # This bounded probe binds exact acquired locators, not arbitrary SEC pages.
    if record["source_locator"] != EXPECTED_SOURCES[record["filename"]]:
        raise ValueError("source locator mismatch")
    receipt = record["receipt_utc"]
    if not isinstance(receipt, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", receipt
    ):
        raise ValueError("receipt must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(receipt.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid receipt timestamp") from exc
    if parsed > datetime.now(timezone.utc):
        raise ValueError("receipt timestamp is in the future")
    if record["content_type"] != "text/plain":
        raise ValueError("unexpected submission content type")

def _sha256(payload: bytes) -> str: return hashlib.sha256(payload).hexdigest()
def _plain(payload: bytes) -> str: return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(payload.decode("latin-1")))).strip()

def _validate_submission_identity(filename: str, payload: bytes) -> None:
    """Bind the retained artifact to its SEC submission-header identity."""
    text = payload.decode("latin-1")
    identity = SUBMISSION_IDENTITIES[filename]
    anchors = (
        f"<SEC-DOCUMENT>{identity['accession']}.txt",
        f"<SEC-HEADER>{identity['accession']}.hdr.sgml",
        f"<ACCEPTANCE-DATETIME>{identity['acceptance_datetime']}",
        f"ACCESSION NUMBER:\t\t{identity['accession']}",
        f"CONFORMED SUBMISSION TYPE:\t{identity['submission_type']}",
        f"PUBLIC DOCUMENT COUNT:\t\t{identity['public_document_count']}",
        "CENTRAL INDEX KEY:\t\t\t0001418091",
        f"SEC FILE NUMBER:\t{identity['file_number']}",
    )
    if any(anchor not in text for anchor in anchors):
        raise ValueError(f"SEC submission identity mismatch: {filename}")

def _load(root: Path) -> dict[str, bytes]:
    raw = (root / "manifest.json").read_bytes()
    if (root / "manifest.sha256").read_text(encoding="ascii").strip() != f"{_sha256(raw)}  manifest.json": raise ValueError("manifest SHA256 sidecar mismatch")
    manifest = json.loads(raw)
    if manifest.get("schema") != "er1c-sec-twitter-submission-probe-v1": raise ValueError("unexpected manifest schema")
    if not isinstance(manifest.get("records"), list) or len(manifest["records"]) != len(EXPECTED_FILES): raise ValueError("unexpected manifest records")
    if {item.name for item in root.iterdir() if item.is_file()} != EXPECTED_FILES | {"manifest.json", "manifest.sha256"}: raise ValueError("probe inventory does not match manifest scope")
    expected_keys = {"bytes", "content_type", "filename", "historical_availability_proven", "http_status", "receipt_utc", "sha256", "source_locator"}; payloads = {}
    for record in manifest["records"]:
        if not isinstance(record, dict) or set(record) != expected_keys: raise ValueError("unexpected evidence-record schema")
        filename = record["filename"]
        if filename in payloads or filename not in EXPECTED_FILES: raise ValueError("unexpected or duplicate evidence filename")
        if record["http_status"] != 200 or record["historical_availability_proven"] is not False: raise ValueError("invalid retrieval or availability classification")
        _validate_provenance(record)
        payload = (root / filename).read_bytes()
        if record["bytes"] != len(payload) or record["sha256"] != _sha256(payload): raise ValueError(f"artifact integrity mismatch: {filename}")
        payloads[filename] = payload
    return payloads

def audit_probe(root: Path) -> dict:
    payloads = _load(root)
    for filename, payload in payloads.items(): _validate_submission_identity(filename, payload)
    registration = _plain(payloads["twitter_20220418_8a12b_submission.txt"]); removal = _plain(payloads["twitter_20221028_25nse_submission.txt"])
    if any(anchor not in registration for anchor in ("FORM TYPE: 8-A12B", "FILED AS OF DATE: 20220418", "Preferred Stock Purchase Rights", "New York Stock Exchange")): raise ValueError("Twitter 8-A12B scope mismatch")
    removal_raw = payloads["twitter_20221028_25nse_submission.txt"].decode("latin-1")
    anchors = ("FORM TYPE: 25-NSE", "FILED AS OF DATE: 20221028", "EFFECTIVENESS DATE: 20221028", "NEW YORK STOCK EXCHANGE LLC", "TWITTER, INC.", "opening of business on November 08, 2022", "merger between Twitter, Inc. and X Holdings II, Inc.", "became effective on October 27, 2022", "suspended from trading before market open on October 28, 2022")
    if any(anchor not in removal for anchor in anchors) or "<descriptionClassSecurity>Common Stock</descriptionClassSecurity>" not in removal_raw: raise ValueError("Twitter 25-NSE scope mismatch")
    return {"source_classification": "AUTHORITATIVE_WITHIN_EXACT_SEC_SUBMISSION_SCOPE", "registration_finding": {"filed_date": "2022-04-18", "security_class": "Preferred Stock Purchase Rights", "exchange": "New York Stock Exchange", "common_stock_listing_event": False}, "removal_finding": {"filed_date": "2022-10-28", "issuer_cik": "0001418091", "security_class": "Common Stock", "exchange": "New York Stock Exchange LLC", "merger_effective_date": "2022-10-27", "trading_suspended_before_open": "2022-10-28", "removal_from_listing_and_registration": "2022-11-08"}, "historical_xnys_universe": "NO_GO", "complete_xnys_listing_change_ledger": "NO_GO", "limitations": ["the 8-A12B registers preferred-stock purchase rights, not Twitter common stock", "the 25-NSE establishes this issue-specific removal and suspension only", "filing, merger, suspension, and removal dates have distinct meanings", "current receipt does not prove historical pre-decision availability"]}

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("root", type=Path); args = parser.parse_args(); print(json.dumps(audit_probe(args.root), indent=2, sort_keys=True))
if __name__ == "__main__": main()
