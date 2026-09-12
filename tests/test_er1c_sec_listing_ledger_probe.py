import hashlib, json
from pathlib import Path
import pytest
from tools.audit_er1c_sec_listing_ledger_probe import INDEX_FILES, audit_probe

def _row(form, company, cik, filed, locator):
    return f"{form:<12}     {company:<62} {cik:<12} {filed:<11} {locator}\n"

def _index(last_date, rows=()):
    return ("Description:           Master Index of EDGAR Dissemination Feed by Form Type\n" + f"Last Data Received:    {last_date}\n" + "".join(rows)).encode("latin-1")

def _write_probe(root: Path):
    artifacts = {"sec_accessing_edgar_data.html": b"do not guarantee accuracy or scope", "sec_exchange_delistings.html": b"Exchange Delistings are filed through EDGAR on Form 25-NSE"}
    for filename, last_date in INDEX_FILES.items():
        rows = []
        if filename == "sec_2022_q2_form.idx": rows = [_row("8-A12B", "TWITTER, INC.", "1418091", "2022-04-18", "edgar/data/1418091/0001193125-22-107480.txt")]
        if filename == "sec_2022_q4_form.idx": rows = [_row("25-NSE", "TWITTER, INC.", "1418091", "2022-10-28", "edgar/data/1418091/0000876661-22-000890.txt")]
        artifacts[filename] = _index(last_date, rows)
    records = []
    for filename, payload in artifacts.items():
        (root / filename).write_bytes(payload)
        records.append({"bytes": len(payload), "content_type": "text/plain", "filename": filename, "historical_availability_proven": False, "http_last_modified": None, "http_status": 200, "receipt_utc": "2026-09-12T00:00:00Z", "sha256": hashlib.sha256(payload).hexdigest(), "source_locator": f"https://www.sec.gov/{filename}"})
    manifest = {"schema": "er1c-sec-listing-ledger-probe-v1", "records": records}
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(); (root / "manifest.json").write_bytes(raw); (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")

@pytest.fixture
def probe(tmp_path): _write_probe(tmp_path); return tmp_path

def _rewrite(root, mutate):
    path = root / "manifest.json"; doc = json.loads(path.read_bytes()); mutate(doc)
    raw = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode(); path.write_bytes(raw); (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")

def _rehash(root, filename, payload):
    (root / filename).write_bytes(payload)
    def update(doc): next(x for x in doc["records"] if x["filename"] == filename).update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    _rewrite(root, update)

def test_reports_filing_evidence_without_roster_admission(probe):
    result = audit_probe(probe)
    assert result["historical_xnys_universe"] == result["complete_xnys_listing_change_ledger"] == "NO_GO"
    assert [row["form"] for row in result["twitter_rows"]] == ["8-A12B", "25-NSE"]

def test_rejects_tampered_artifact(probe):
    (probe / "sec_2022_q2_form.idx").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity mismatch"): audit_probe(probe)

def test_rejects_undeclared_inventory(probe):
    (probe / "extra.txt").write_text("extra")
    with pytest.raises(ValueError, match="inventory"): audit_probe(probe)

def test_rejects_availability_overclaim(probe):
    _rewrite(probe, lambda doc: doc["records"][0].update(historical_availability_proven=True))
    with pytest.raises(ValueError, match="availability"): audit_probe(probe)

def test_rejects_wrong_index_edition(probe):
    name = "sec_2022_q2_form.idx"; _rehash(probe, name, (probe / name).read_bytes().replace(b"June 30", b"June 29"))
    with pytest.raises(ValueError, match="edition mismatch"): audit_probe(probe)

def test_rejects_missing_twitter_removal(probe):
    name = "sec_2022_q4_form.idx"; _rehash(probe, name, _index(INDEX_FILES[name]))
    with pytest.raises(ValueError, match="Twitter"): audit_probe(probe)
