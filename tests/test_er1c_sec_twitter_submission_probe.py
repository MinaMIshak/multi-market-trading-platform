import hashlib, json
from pathlib import Path
import pytest
from tools.audit_er1c_sec_twitter_submission_probe import EXPECTED_SOURCES, audit_probe

def _header(accession, accepted, form, count):
    return f"""<SEC-DOCUMENT>{accession}.txt : 20221028
<SEC-HEADER>{accession}.hdr.sgml : 20221028
<ACCEPTANCE-DATETIME>{accepted}
ACCESSION NUMBER:\t\t{accession}
CONFORMED SUBMISSION TYPE:\t{form}
PUBLIC DOCUMENT COUNT:\t\t{count}
CENTRAL INDEX KEY:\t\t\t0001418091
SEC FILE NUMBER:\t001-36164
""".encode()

REGISTRATION = _header("0001193125-22-107480", "20220418093659", "8-A12B", "1") + b"FILED AS OF DATE: 20220418 FORM TYPE: 8-A12B <td>Preferred Stock Purchase Rights</td><td>New York Stock Exchange</td>"
REMOVAL = _header("0000876661-22-000890", "20221028083119", "25-NSE", "2") + b"FILED AS OF DATE: 20221028 EFFECTIVENESS DATE: 20221028 FORM TYPE: 25-NSE NEW YORK STOCK EXCHANGE LLC TWITTER, INC. <descriptionClassSecurity>Common Stock</descriptionClassSecurity> opening of business on November 08, 2022 merger between Twitter, Inc. and X Holdings II, Inc. became effective on October 27, 2022 suspended from trading before market open on October 28, 2022"
def _write_probe(root: Path):
    artifacts = {"twitter_20220418_8a12b_submission.txt": REGISTRATION, "twitter_20221028_25nse_submission.txt": REMOVAL}; records = []
    for filename, payload in artifacts.items():
        (root / filename).write_bytes(payload); records.append({"bytes": len(payload), "content_type": "text/plain", "filename": filename, "historical_availability_proven": False, "http_status": 200, "receipt_utc": "2026-09-12T00:00:00Z", "sha256": hashlib.sha256(payload).hexdigest(), "source_locator": EXPECTED_SOURCES[filename]})
    raw = (json.dumps({"schema": "er1c-sec-twitter-submission-probe-v1", "records": records}, indent=2, sort_keys=True) + "\n").encode(); (root / "manifest.json").write_bytes(raw); (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")
@pytest.fixture
def probe(tmp_path): _write_probe(tmp_path); return tmp_path
def _rehash(root, filename, payload):
    (root / filename).write_bytes(payload); path = root / "manifest.json"; manifest = json.loads(path.read_bytes()); record = next(item for item in manifest["records"] if item["filename"] == filename); record.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()); raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(); path.write_bytes(raw); (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")
def test_distinguishes_rights_registration_from_common_stock_removal(probe):
    result = audit_probe(probe); assert result["registration_finding"]["common_stock_listing_event"] is False; assert result["registration_finding"]["security_class"] == "Preferred Stock Purchase Rights"; assert result["removal_finding"]["security_class"] == "Common Stock"; assert result["historical_xnys_universe"] == "NO_GO"
def test_preserves_distinct_merger_suspension_and_removal_dates(probe):
    finding = audit_probe(probe)["removal_finding"]; assert finding["merger_effective_date"] == "2022-10-27"; assert finding["trading_suspended_before_open"] == "2022-10-28"; assert finding["removal_from_listing_and_registration"] == "2022-11-08"
def test_rejects_tampered_artifact(probe):
    (probe / "twitter_20221028_25nse_submission.txt").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity mismatch"): audit_probe(probe)
def test_rejects_common_stock_misclassification(probe):
    _rehash(probe, "twitter_20220418_8a12b_submission.txt", REGISTRATION.replace(b"Preferred Stock Purchase Rights", b"Common Stock"))
    with pytest.raises(ValueError, match="8-A12B scope mismatch"): audit_probe(probe)
def test_rejects_missing_issue_specific_suspension(probe):
    _rehash(probe, "twitter_20221028_25nse_submission.txt", REMOVAL.replace(b"suspended from trading", b"removed from trading"))
    with pytest.raises(ValueError, match="25-NSE scope mismatch"): audit_probe(probe)

@pytest.mark.parametrize(("filename", "old", "new"), [
    ("twitter_20220418_8a12b_submission.txt", b"0001193125-22-107480", b"0001193125-22-107481"),
    ("twitter_20220418_8a12b_submission.txt", b"20220418093659", b"20220418093700"),
    ("twitter_20221028_25nse_submission.txt", b"PUBLIC DOCUMENT COUNT:\t\t2", b"PUBLIC DOCUMENT COUNT:\t\t1"),
    ("twitter_20221028_25nse_submission.txt", b"CENTRAL INDEX KEY:\t\t\t0001418091", b"CENTRAL INDEX KEY:\t\t\t0001418092"),
    ("twitter_20221028_25nse_submission.txt", b"SEC FILE NUMBER:\t001-36164", b"SEC FILE NUMBER:\t001-36165"),
])
def test_rejects_rehashed_wrong_submission_identity(probe, filename, old, new):
    payload = (probe / filename).read_bytes().replace(old, new)
    _rehash(probe, filename, payload)
    with pytest.raises(ValueError, match="submission identity"):
        audit_probe(probe)
def test_rejects_undeclared_inventory(probe):
    (probe / "extra.txt").write_text("extra")
    with pytest.raises(ValueError, match="inventory"): audit_probe(probe)


def _change_record(root, key, value):
    path = root / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["records"][0][key] = value
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")


@pytest.mark.parametrize("locator", [
    "https://example.org/submission.txt",
    "https://www.sec.gov/Archives/edgar/data/1418091/unrelated.txt",
    EXPECTED_SOURCES["twitter_20221028_25nse_submission.txt"],
])
def test_rejects_rehashed_wrong_source(probe, locator):
    _change_record(probe, "source_locator", locator)
    with pytest.raises(ValueError, match="source locator"):
        audit_probe(probe)


@pytest.mark.parametrize("receipt", [
    None, "", "2026-09-12", "2026-09-12T00:00:00",
    "2026-09-12T02:00:00+02:00", "2026-02-30T00:00:00Z",
    "9999-01-01T00:00:00Z",
])
def test_rejects_invalid_receipt(probe, receipt):
    _change_record(probe, "receipt_utc", receipt)
    with pytest.raises(ValueError, match="receipt"):
        audit_probe(probe)


def test_rejects_unexpected_content_type(probe):
    _change_record(probe, "content_type", "text/html")
    with pytest.raises(ValueError, match="content type"):
        audit_probe(probe)
