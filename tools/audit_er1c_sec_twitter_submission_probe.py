"""Offline audit for the bounded ER1C Twitter SEC submission probe."""
from __future__ import annotations
import argparse, hashlib, html, json, re, stat
from pathlib import Path
from datetime import datetime, timezone

EXPECTED_SOURCES = {
    "twitter_20220418_8a12b_submission.txt": "https://www.sec.gov/Archives/edgar/data/1418091/000119312522107480/0001193125-22-107480.txt?output=1",
    "twitter_20221028_25nse_submission.txt": "https://www.sec.gov/Archives/edgar/data/1418091/000087666122000890/0000876661-22-000890.txt?output=1",
}
EXPECTED_FILES = set(EXPECTED_SOURCES)
PACKAGE_FILES = EXPECTED_FILES | {"manifest.json", "manifest.sha256"}
PURPOSE = (
    "Bounded submission-level classification of the two Twitter filings identified "
    "by the retained EDGAR indexes; not a complete listing ledger or historical universe"
)
SUBMISSION_IDENTITIES = {
    "twitter_20220418_8a12b_submission.txt": {
        "accession": "0001193125-22-107480", "acceptance_datetime": "20220418093659",
        "submission_type": "8-A12B", "public_document_count": "1", "file_number": "001-36164",
        "filed_as_of_date": "20220418", "effectiveness_date": None,
    },
    "twitter_20221028_25nse_submission.txt": {
        "accession": "0000876661-22-000890", "acceptance_datetime": "20221028083119",
        "submission_type": "25-NSE", "public_document_count": "2", "file_number": "001-36164",
        "filed_as_of_date": "20221028", "effectiveness_date": "20221028",
    },
}
EXPECTED_DOCUMENTS = {
    "twitter_20220418_8a12b_submission.txt": (
        ("8-A12B", "1", "d303512d8a12b.htm"),
    ),
    "twitter_20221028_25nse_submission.txt": (
        ("25-NSE", "1", "primary_doc.xml"),
        ("EX-99.25", "2", "ruleprovisionnotice.htm"),
    ),
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

def _validate_custody(root: Path) -> None:
    try:
        root_stat = root.lstat()
    except FileNotFoundError as exc:
        raise ValueError("probe root must be a real directory") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink():
        raise ValueError("probe root must be a real directory")
    entries = {item.name: item for item in root.iterdir()}
    if set(entries) != PACKAGE_FILES:
        raise ValueError("probe inventory does not match manifest scope")
    for path in entries.values():
        entry_stat = path.lstat()
        if not stat.S_ISREG(entry_stat.st_mode) or path.is_symlink():
            raise ValueError("probe entries must be regular files")
        if entry_stat.st_nlink != 1:
            raise ValueError("probe entries must have link count one")

def _validate_submission_identity(filename: str, payload: bytes) -> None:
    """Bind the retained artifact to its SEC submission-header identity."""
    text = payload.decode("latin-1")
    header = text.split("<DOCUMENT>", 1)[0]
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
        f"FILED AS OF DATE:\t\t{identity['filed_as_of_date']}",
    )
    if identity["effectiveness_date"] is not None:
        anchors += (f"EFFECTIVENESS DATE:\t\t{identity['effectiveness_date']}",)
    if any(anchor not in header for anchor in anchors):
        raise ValueError(f"SEC submission identity mismatch: {filename}")

def _documents(filename: str, payload: bytes) -> dict[str, str]:
    """Return exact declared submission documents, keyed by SEC type."""
    text = payload.decode("latin-1")
    blocks = re.findall(r"<DOCUMENT>\s*(.*?)\s*</DOCUMENT>", text, re.DOTALL)
    parsed = []
    for block in blocks:
        fields = []
        for name in ("TYPE", "SEQUENCE", "FILENAME"):
            match = re.search(rf"(?m)^<{name}>([^\r\n]+)$", block)
            if match is None:
                raise ValueError(f"incomplete SEC document envelope: {filename}")
            fields.append(match.group(1).strip())
        parsed.append(tuple(fields))
    if tuple(parsed) != EXPECTED_DOCUMENTS[filename]:
        raise ValueError(f"SEC document inventory mismatch: {filename}")
    return {document_type: block for (document_type, _, _), block in zip(parsed, blocks)}

def _load(root: Path) -> dict[str, bytes]:
    _validate_custody(root)
    raw = (root / "manifest.json").read_bytes()
    if (root / "manifest.sha256").read_text(encoding="ascii").strip() != f"{_sha256(raw)}  manifest.json": raise ValueError("manifest SHA256 sidecar mismatch")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or set(manifest) != {"purpose", "records", "schema"}:
        raise ValueError("unexpected manifest schema")
    if manifest.get("schema") != "er1c-sec-twitter-submission-probe-v1": raise ValueError("unexpected manifest schema")
    if manifest["purpose"] != PURPOSE:
        raise ValueError("unexpected manifest purpose")
    if not isinstance(manifest.get("records"), list) or len(manifest["records"]) != len(EXPECTED_FILES): raise ValueError("unexpected manifest records")
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
    registration_documents = _documents("twitter_20220418_8a12b_submission.txt", payloads["twitter_20220418_8a12b_submission.txt"])
    removal_documents = _documents("twitter_20221028_25nse_submission.txt", payloads["twitter_20221028_25nse_submission.txt"])
    registration = _plain(registration_documents["8-A12B"].encode("latin-1"))
    if any(anchor not in registration for anchor in ("Preferred Stock Purchase Rights", "New York Stock Exchange")): raise ValueError("Twitter 8-A12B scope mismatch")
    primary_raw = removal_documents["25-NSE"]
    primary = _plain(primary_raw.encode("latin-1"))
    primary_anchors = ("NEW YORK STOCK EXCHANGE LLC", "TWITTER, INC.")
    notice = _plain(removal_documents["EX-99.25"].encode("latin-1"))
    notice_anchors = ("opening of business on November 08, 2022", "merger between Twitter, Inc. and X Holdings II, Inc.", "became effective on October 27, 2022", "suspended from trading before market open on October 28, 2022")
    if (any(anchor not in primary for anchor in primary_anchors)
            or "<descriptionClassSecurity>Common Stock</descriptionClassSecurity>" not in primary_raw
            or any(anchor not in notice for anchor in notice_anchors)):
        raise ValueError("Twitter 25-NSE scope mismatch")
    return {"source_classification": "AUTHORITATIVE_WITHIN_EXACT_SEC_SUBMISSION_SCOPE", "registration_finding": {"filed_date": "2022-04-18", "security_class": "Preferred Stock Purchase Rights", "exchange": "New York Stock Exchange", "common_stock_listing_event": False}, "removal_finding": {"filed_date": "2022-10-28", "issuer_cik": "0001418091", "security_class": "Common Stock", "exchange": "New York Stock Exchange LLC", "merger_effective_date": "2022-10-27", "trading_suspended_before_open": "2022-10-28", "removal_from_listing_and_registration": "2022-11-08"}, "historical_xnys_universe": "NO_GO", "complete_xnys_listing_change_ledger": "NO_GO", "limitations": ["the 8-A12B registers preferred-stock purchase rights, not Twitter common stock", "the 25-NSE establishes this issue-specific removal and suspension only", "filing, merger, suspension, and removal dates have distinct meanings", "current receipt does not prove historical pre-decision availability"]}

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("root", type=Path); args = parser.parse_args(); print(json.dumps(audit_probe(args.root), indent=2, sort_keys=True))
if __name__ == "__main__": main()
