"""Audit a return-independent SEC form sample declaration against retained indexes."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


FORMS = ("8-A12B", "25-NSE")
INDEX_FILES = (
    "sec_2022_q2_form.idx",
    "sec_2022_q3_form.idx",
    "sec_2022_q4_form.idx",
)
SEED = "er1c-sec-form-sample-v1"
ALGORITHM = (
    "For each retained 2022 Q2-Q4 index and each form in [8-A12B, 25-NSE], "
    "choose lexicographically smallest SHA256 of seed + NUL + submission_locator"
)
INDEX_ROW = re.compile(
    r"^(\S+)\s+(.+?)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(edgar/data/\S+)\s*$"
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _expected_records(index_root: Path) -> list[dict]:
    selected = []
    for filename in INDEX_FILES:
        payload = (index_root / filename).read_bytes()
        index_hash = _sha256(payload)
        quarter = filename[9:11].lower()
        lines = payload.decode("latin-1").splitlines()
        for target_form in FORMS:
            candidates = []
            for line_number, line in enumerate(lines, start=1):
                if line[:12].strip() != target_form:
                    continue
                match = INDEX_ROW.match(line)
                if match is None:
                    raise ValueError(f"malformed target-form row: {filename}:{line_number}")
                rank = _sha256(f"{SEED}\0{match.group(5)}".encode())
                candidates.append((rank, line_number, match.groups()))
            if not candidates:
                raise ValueError(f"target form absent: {filename}:{target_form}")
            rank, line_number, (form, company, cik, filed, locator) = min(candidates)
            accession = locator.rsplit("/", 1)[1].removesuffix(".txt")
            source = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession.replace('-', '')}/{accession}.txt?output=1"
            )
            selected.append(
                {
                    "cik": cik,
                    "company": company,
                    "date_filed": filed,
                    "filename": f"{quarter}_{form.lower().replace('-', '')}_{cik}_{accession}.txt",
                    "form": form,
                    "index_filename": filename,
                    "index_line": line_number,
                    "index_sha256": index_hash,
                    "rank_sha256": rank,
                    "source_locator": source,
                    "submission_locator": locator,
                }
            )
    return selected


def audit_declaration(root: Path, index_root: Path) -> dict:
    raw = (root / "declaration.json").read_bytes()
    sidecar = (root / "declaration.sha256").read_text(encoding="ascii").strip()
    if sidecar != f"{_sha256(raw)}  declaration.json":
        raise ValueError("declaration SHA256 sidecar mismatch")
    declaration = json.loads(raw)
    if set(declaration) != {
        "purpose",
        "records",
        "schema",
        "selection_algorithm",
        "selection_seed",
    }:
        raise ValueError("unexpected declaration schema")
    if declaration["schema"] != "er1c-sec-form-sample-declaration-v1":
        raise ValueError("unexpected declaration version")
    if declaration["selection_seed"] != SEED:
        raise ValueError("unexpected selection seed")
    if declaration["selection_algorithm"] != ALGORITHM:
        raise ValueError("unexpected selection algorithm")
    expected = _expected_records(index_root)
    if declaration["records"] != expected:
        raise ValueError("declared sample does not match deterministic index selection")
    return {
        "declaration_sha256": _sha256(raw),
        "forms": list(FORMS),
        "quarters": len(INDEX_FILES),
        "sample_size": len(expected),
        "selection_verified": True,
        "submission_contents_used_for_selection": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("index_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_declaration(args.root, args.index_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
