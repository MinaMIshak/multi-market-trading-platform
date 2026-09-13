"""Audit the retained Nasdaq halt-feed terms package and fail closed on use."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


PURPOSE = (
    "Documentation and terms qualification only; no halt feed acquisition or "
    "session admission."
)
EXPECTED = {
    "rss_documentation.html": (
        "https://www.nasdaqtrader.com/Trader.aspx?id=TradeHaltRSS",
        50420,
        "7acea06e83f9d40cdd0cb95cd2ed9d04601cfa068fc414db213c0a3a38e761a2",
    ),
    "rss_terms.pdf": (
        "https://www.nasdaqtrader.com/content/administrationsupport/agreementstrading/THRSSFeedTermsCond.pdf",
        75440,
        "9f3e25671be08d33b81785dac0eae63fc374904debcf2452aa1752964b3751b1",
    ),
    "rss_queries.html": (
        "https://www.nasdaqtrader.com/snippets/tradehaltaccordion.html",
        4369,
        "bbe019d76538d86e84826b06b35da110f56ea3e709621828985e15422f8d3217",
    ),
}


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} is not a timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{label} is not explicit UTC")
    return parsed.astimezone(timezone.utc)


def audit_halt_terms(root: Path, *, audited_at: datetime | None = None) -> dict:
    """Bind the exact reviewed edition; do not infer rights absent from its text."""
    audited_at = audited_at or datetime.now(timezone.utc)
    if type(audited_at) is not datetime or audited_at.tzinfo is None:
        raise ValueError("audited_at must be timezone-aware")
    audited_at = audited_at.astimezone(timezone.utc)

    manifest_bytes = (root / "manifest.json").read_bytes()
    sidecar = (root / "manifest.sha256").read_text(encoding="ascii").strip()
    if sidecar != f"{_sha(manifest_bytes)}  manifest.json":
        raise ValueError("manifest SHA256 sidecar mismatch")
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "purpose", "records"}:
        raise ValueError("unexpected manifest fields")
    if manifest["schema"] != "er1c-nasdaq-halt-qualification-v1" or manifest["purpose"] != PURPOSE:
        raise ValueError("unexpected manifest scope")
    records = manifest["records"]
    if not isinstance(records, list) or len(records) != len(EXPECTED):
        raise ValueError("unexpected artifact records")
    inventory = {p.name for p in root.iterdir() if p.is_file()}
    if inventory != set(EXPECTED) | {"manifest.json", "manifest.sha256"}:
        raise ValueError("artifact inventory does not match bounded scope")

    required = {
        "filename", "source_locator", "resolved_locator", "http_status", "bytes",
        "sha256", "retrieval_started_at_utc", "retrieval_completed_at_utc",
        "historical_availability_proven",
    }
    receipts: list[datetime] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError("unexpected artifact-record schema")
        name = record["filename"]
        if name in seen or name not in EXPECTED:
            raise ValueError("unexpected or duplicate artifact")
        seen.add(name)
        locator, size, digest = EXPECTED[name]
        if record["source_locator"] != locator or record["resolved_locator"] != locator:
            raise ValueError("unexpected locator or redirect")
        if record["http_status"] != 200 or record["historical_availability_proven"] is not False:
            raise ValueError("retrieval status or historical-availability claim changed")
        started = _utc(record["retrieval_started_at_utc"], "request start")
        completed = _utc(record["retrieval_completed_at_utc"], "receipt completion")
        if completed < started or completed > audited_at:
            raise ValueError("invalid artifact receipt ordering")
        payload = (root / name).read_bytes()
        if (record["bytes"], record["sha256"]) != (size, digest):
            raise ValueError(f"reviewed edition mismatch: {name}")
        if len(payload) != size or _sha(payload) != digest:
            raise ValueError(f"artifact integrity mismatch: {name}")
        receipts.append(completed)

    if not (root / "rss_terms.pdf").read_bytes().startswith(b"%PDF-"):
        raise ValueError("terms artifact is not a PDF")
    return {
        "source": "Nasdaq Trader",
        "latest_receipt_at": max(receipts).isoformat(),
        "reviewed_terms_sha256": EXPECTED["rss_terms.pdf"][2],
        "documentation_integrity": "PASS",
        "feed_requests_made": 0,
        "raw_retention_permission": "UNRESOLVED",
        "transformation_permission": "UNRESOLVED",
        "feed_acquisition": "NO_GO",
        "canonical_admission": "NO_GO",
        "reason": (
            "The reviewed terms do not expressly grant the archival retention and "
            "transformation required by the research protocol."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_halt_terms(args.root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
