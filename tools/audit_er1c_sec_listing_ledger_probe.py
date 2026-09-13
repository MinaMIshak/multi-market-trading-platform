"""Offline audit for the bounded ER1C SEC listing-ledger probe."""
from __future__ import annotations
import argparse, hashlib, json, re, stat
from pathlib import Path

INDEX_FILES = {"sec_2022_q2_form.idx": "June 30, 2022", "sec_2022_q3_form.idx": "September 30, 2022", "sec_2022_q4_form.idx": "December 31, 2022"}
GUIDE_FILES = {"sec_accessing_edgar_data.html", "sec_exchange_delistings.html"}
EXPECTED_FILES = set(INDEX_FILES) | GUIDE_FILES
PACKAGE_FILES = EXPECTED_FILES | {"manifest.json", "manifest.sha256"}
TARGET_FORMS = {"25-NSE", "8-A12B", "8-A12B/A"}
INDEX_ROW = re.compile(r"^(\S+)\s+(.+?)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(edgar/data/\S+)\s*$")

def _sha256(payload: bytes) -> str: return hashlib.sha256(payload).hexdigest()

def _validate_custody(root: Path) -> None:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise ValueError("probe root is unavailable") from exc
    if root.is_symlink() or not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("probe root must be a real directory")
    entries = {entry.name: entry for entry in root.iterdir()}
    if set(entries) != PACKAGE_FILES:
        raise ValueError("probe inventory does not match manifest scope")
    for name, entry in entries.items():
        entry_stat = entry.lstat()
        if entry.is_symlink() or not stat.S_ISREG(entry_stat.st_mode):
            raise ValueError(f"probe entry must be a regular non-symlink file: {name}")
        if entry_stat.st_nlink != 1:
            raise ValueError(f"probe entry must not be hard linked: {name}")

def _load_manifest(root: Path) -> dict:
    raw = (root / "manifest.json").read_bytes()
    if (root / "manifest.sha256").read_text(encoding="ascii").strip() != f"{_sha256(raw)}  manifest.json":
        raise ValueError("manifest SHA256 sidecar mismatch")
    manifest = json.loads(raw)
    if manifest.get("schema") != "er1c-sec-listing-ledger-probe-v1": raise ValueError("unexpected manifest schema")
    if not isinstance(manifest.get("records"), list) or len(manifest["records"]) != len(EXPECTED_FILES): raise ValueError("unexpected manifest records")
    return manifest

def _parse_index(payload: bytes, filename: str):
    text = payload.decode("latin-1")
    if "Master Index of EDGAR Dissemination Feed by Form Type" not in text: raise ValueError(f"EDGAR form-index scope mismatch: {filename}")
    if f"Last Data Received:    {INDEX_FILES[filename]}" not in text: raise ValueError(f"EDGAR form-index edition mismatch: {filename}")
    counts, twitter = {form: 0 for form in TARGET_FORMS}, []
    for number, line in enumerate(text.splitlines(), start=1):
        form = line[:12].strip()
        if form not in TARGET_FORMS: continue
        counts[form] += 1
        match = INDEX_ROW.match(line)
        if match is None: raise ValueError(f"malformed target-form row: {filename}:{number}")
        parsed_form, company, cik, filed, locator = match.groups()
        if parsed_form != form: raise ValueError(f"malformed target-form row: {filename}:{number}")
        if cik == "1418091": twitter.append({"company": company, "date_filed": filed, "form": form, "line": number, "submission_locator": locator})
    return counts, twitter

def audit_probe(root: Path) -> dict:
    _validate_custody(root)
    manifest = _load_manifest(root)
    payloads = {}
    expected_keys = {"bytes", "content_type", "filename", "historical_availability_proven", "http_last_modified", "http_status", "receipt_utc", "sha256", "source_locator"}
    for record in manifest["records"]:
        if not isinstance(record, dict) or set(record) != expected_keys: raise ValueError("unexpected evidence-record schema")
        filename = record["filename"]
        if filename in payloads or filename not in EXPECTED_FILES: raise ValueError("unexpected or duplicate evidence filename")
        if record["http_status"] != 200 or record["historical_availability_proven"] is not False: raise ValueError("invalid retrieval or availability classification")
        payload = (root / filename).read_bytes()
        if record["bytes"] != len(payload) or record["sha256"] != _sha256(payload): raise ValueError(f"artifact integrity mismatch: {filename}")
        payloads[filename] = payload
    if "do not guarantee accuracy or scope" not in payloads["sec_accessing_edgar_data.html"].decode("utf-8"): raise ValueError("SEC ticker/exchange limitation missing")
    if "Exchange Delistings are filed through EDGAR on Form 25-NSE" not in payloads["sec_exchange_delistings.html"].decode("utf-8"): raise ValueError("SEC delisting guidance scope mismatch")
    indexes, twitter = {}, []
    for filename in INDEX_FILES:
        indexes[filename], matches = _parse_index(payloads[filename], filename); twitter.extend(matches)
    expected = [
        {"company": "TWITTER, INC.", "date_filed": "2022-04-18", "form": "8-A12B", "submission_locator": "edgar/data/1418091/0001193125-22-107480.txt"},
        {"company": "TWITTER, INC.", "date_filed": "2022-10-28", "form": "25-NSE", "submission_locator": "edgar/data/1418091/0000876661-22-000890.txt"},
    ]
    comparable = [{key: row[key] for key in expected[0]} for row in twitter]
    if comparable != expected or any(row["line"] < 1 for row in twitter): raise ValueError("unexpected Twitter registration/delisting index rows")
    return {"source_classification": "AUTHORITATIVE_WITHIN_EDGAR_FILING_INDEX_SCOPE", "historical_xnys_universe": "NO_GO", "complete_xnys_listing_change_ledger": "NO_GO", "index_target_form_counts": indexes, "twitter_rows": twitter,
            "limitations": ["Form 25-NSE enumerates exchange-removal filings, not every roster member or trading status", "8-A12B filings do not alone establish initial trading date, security eligibility, or XNYS venue", "form indexes identify filings but require submission-level parsing to determine exchange and class", "EDGAR ticker/exchange associations are periodically updated and have no guaranteed accuracy or scope", "current receipt does not prove historical pre-decision availability"]}

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("root", type=Path); args = parser.parse_args()
    print(json.dumps(audit_probe(args.root), indent=2, sort_keys=True))
if __name__ == "__main__": main()
