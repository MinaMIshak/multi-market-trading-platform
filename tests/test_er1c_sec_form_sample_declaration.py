import hashlib
import json
from pathlib import Path

import pytest

from tools.audit_er1c_sec_form_sample_declaration import ALGORITHM, FORMS, INDEX_FILES, SEED, audit_declaration


def _row(form: str, company: str, cik: str, date: str, locator: str) -> str:
    return f"{form:<12}{company:<62}{cik:>12}  {date}  {locator}"


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    indexes = tmp_path / "indexes"
    declaration_root = tmp_path / "sample"
    indexes.mkdir()
    declaration_root.mkdir()
    records = []
    for quarter_number, filename in enumerate(INDEX_FILES, start=2):
        rows = []
        for form in FORMS:
            for candidate in ("a", "b"):
                cik = str(quarter_number * 100 + len(rows) + 1)
                locator = f"edgar/data/{cik}/0000000-22-00000{candidate == 'b'}.txt"
                rows.append((form, f"Issuer {candidate}", cik, "2022-01-01", locator))
        payload = ("\n".join(_row(*row) for row in rows) + "\n").encode("latin-1")
        (indexes / filename).write_bytes(payload)
        for form in FORMS:
            candidates = []
            for line_number, row in enumerate(rows, start=1):
                if row[0] == form:
                    rank = hashlib.sha256(f"{SEED}\0{row[4]}".encode()).hexdigest()
                    candidates.append((rank, line_number, row))
            rank, line_number, (selected_form, company, cik, filed, locator) = min(candidates)
            accession = locator.rsplit("/", 1)[1][:-4]
            records.append({
                "cik": cik, "company": company, "date_filed": filed,
                "filename": f"q{quarter_number}_{selected_form.lower().replace('-', '')}_{cik}_{accession}.txt",
                "form": selected_form, "index_filename": filename, "index_line": line_number,
                "index_sha256": hashlib.sha256(payload).hexdigest(), "rank_sha256": rank,
                "source_locator": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}.txt?output=1",
                "submission_locator": locator,
            })
    declaration = {
        "schema": "er1c-sec-form-sample-declaration-v1",
        "purpose": "test",
        "selection_algorithm": ALGORITHM,
        "selection_seed": SEED,
        "records": records,
    }
    raw = (json.dumps(declaration, indent=2, sort_keys=True) + "\n").encode()
    (declaration_root / "declaration.json").write_bytes(raw)
    (declaration_root / "declaration.sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  declaration.json\n", encoding="ascii"
    )
    return declaration_root, indexes


def test_accepts_deterministic_sample(tmp_path):
    root, indexes = _write_fixture(tmp_path)
    result = audit_declaration(root, indexes)
    assert result["sample_size"] == 6
    assert result["submission_contents_used_for_selection"] is False


def test_rejects_reselected_record(tmp_path):
    root, indexes = _write_fixture(tmp_path)
    declaration = json.loads((root / "declaration.json").read_text())
    declaration["records"][0]["company"] = "Outcome-selected issuer"
    raw = (json.dumps(declaration, indent=2, sort_keys=True) + "\n").encode()
    (root / "declaration.json").write_bytes(raw)
    (root / "declaration.sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  declaration.json\n", encoding="ascii"
    )
    with pytest.raises(ValueError, match="deterministic index selection"):
        audit_declaration(root, indexes)


def test_rejects_changed_index_edition(tmp_path):
    root, indexes = _write_fixture(tmp_path)
    with (indexes / INDEX_FILES[0]).open("ab") as handle:
        handle.write(b"changed\n")
    with pytest.raises(ValueError, match="deterministic index selection"):
        audit_declaration(root, indexes)
