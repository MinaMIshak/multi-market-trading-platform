import hashlib
import json
from pathlib import Path

import pytest

from tools.audit_er1c_nyse_roster_probe import audit_probe


DATES = ("20220701", "20221027", "20221028")


def _write_probe(root: Path) -> None:
    artifacts = {
        "nyse_symbol_mapping_index.html": b"<title>Index of /NYSESymbolMapping/</title>current only",
    }
    for day in DATES:
        twtr = b"" if day == "20221028" else f"{day}|TWTR|1|2|10|N\n".encode()
        artifacts[f"nyse_short_volume_{day}.txt"] = (
            b"Date|Symbol|Short Exempt Volume|Short Volume|Total Volume|Market\n"
            + f"{day}|IBM|1|2|10|N\n".encode()
            + twtr
        )
    records = []
    for filename, payload in artifacts.items():
        (root / filename).write_bytes(payload)
        records.append({
            "bytes": len(payload), "content_type": "text/plain", "filename": filename,
            "historical_availability_proven": False, "http_last_modified": None,
            "http_status": 200, "receipt_utc": "2026-09-12T00:00:00Z",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "source_locator": f"https://ftp.nyse.com/{filename}",
        })
    manifest = {"schema": "er1c-nyse-roster-probe-v1", "purpose": "fixture", "records": records}
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")


@pytest.fixture
def probe(tmp_path):
    _write_probe(tmp_path)
    return tmp_path


def _rewrite_manifest(root, mutate):
    path = root / "manifest.json"
    manifest = json.loads(path.read_bytes())
    mutate(manifest)
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    (root / "manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")


def test_reports_bounded_presence_without_roster_admission(probe):
    result = audit_probe(probe)
    assert result["historical_xnys_universe"] == "NO_GO"
    assert result["observations"]["20221027"]["TWTR"]["total_volume"] == 10
    assert result["observations"]["20221028"]["TWTR"] is None


def test_rejects_tampered_artifact(probe):
    (probe / "nyse_short_volume_20220701.txt").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity mismatch"):
        audit_probe(probe)


def test_rejects_undeclared_inventory(probe):
    (probe / "extra.txt").write_text("extra")
    with pytest.raises(ValueError, match="inventory"):
        audit_probe(probe)


def test_rejects_availability_overclaim(probe):
    _rewrite_manifest(probe, lambda doc: doc["records"][0].update(historical_availability_proven=True))
    with pytest.raises(ValueError, match="availability"):
        audit_probe(probe)


def test_rejects_duplicate_market_symbol(probe):
    path = probe / "nyse_short_volume_20221027.txt"
    payload = path.read_bytes() + b"20221027|IBM|1|2|10|N\n"
    path.write_bytes(payload)
    def update(doc):
        record = next(x for x in doc["records"] if x["filename"] == path.name)
        record.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    _rewrite_manifest(probe, update)
    with pytest.raises(ValueError, match="duplicate"):
        audit_probe(probe)


def test_rejects_incoherent_volume(probe):
    path = probe / "nyse_short_volume_20221027.txt"
    payload = path.read_bytes().replace(b"20221027|IBM|1|2|10|N", b"20221027|IBM|3|2|10|N")
    path.write_bytes(payload)
    def update(doc):
        record = next(x for x in doc["records"] if x["filename"] == path.name)
        record.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    _rewrite_manifest(probe, update)
    with pytest.raises(ValueError, match="incoherent"):
        audit_probe(probe)
