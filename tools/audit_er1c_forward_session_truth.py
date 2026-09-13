"""Offline audit of bounded official NYSE forward-session evidence.

The retained page is useful current schedule corroboration.  It is deliberately
not promoted to canonical US2 history or to a scoreable shadow recommendation.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path


EXPECTED = {
    "nyse_2026_calendar.pdf": (
        "https://www.nyse.com/publicdocs/nyse/"
        "ICE_NYSE_2026_Yearly_Trading_Calendar.pdf"
    ),
    "nyse_hours_calendars.html": "https://www.nyse.com/trade/hours-calendars",
}
MANIFEST_FILES = {"manifest.json", "manifest.sha256"}
MANIFEST_FIELDS = {"purpose", "records", "schema"}
EXPECTED_PURPOSE = (
    "Bounded official-source qualification for requested first US shadow dates; "
    "not canonical US2 admission or a watchlist."
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} is not an explicit UTC timestamp")
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    return parsed


def _visible_html(payload: bytes) -> str:
    try:
        source = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("NYSE hours artifact is not UTF-8") from error
    if "<html" not in source.lower():
        raise ValueError("NYSE hours artifact is not HTML")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", source)).split())


def audit_forward_session_truth(root: Path, *, audited_at: datetime | None = None) -> dict:
    if audited_at is None:
        audited_at = datetime.now(timezone.utc)
    if type(audited_at) is not datetime or audited_at.tzinfo is not timezone.utc:
        raise ValueError("audited_at must use datetime.timezone.utc")
    raw_manifest = (root / "manifest.json").read_bytes()
    expected_sidecar = f"{_sha256(raw_manifest)}  manifest.json"
    if (root / "manifest.sha256").read_text(encoding="ascii").strip() != expected_sidecar:
        raise ValueError("manifest SHA256 sidecar mismatch")
    manifest = json.loads(raw_manifest)
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
        raise ValueError("unexpected manifest fields")
    if manifest.get("schema") != "er1c-forward-session-truth-v1":
        raise ValueError("unexpected manifest schema")
    if manifest.get("purpose") != EXPECTED_PURPOSE:
        raise ValueError("unexpected evidence purpose")
    records = manifest.get("records")
    if not isinstance(records, list) or len(records) != len(EXPECTED):
        raise ValueError("unexpected evidence records")
    actual = {item.name for item in root.iterdir() if item.is_file()}
    if actual != set(EXPECTED) | MANIFEST_FILES:
        raise ValueError("evidence inventory does not match manifest scope")

    payloads: dict[str, bytes] = {}
    for record in records:
        required = {
            "bytes", "filename", "historical_availability_proven", "http_status",
            "retrieval_completed_at_utc", "retrieval_started_at_utc", "sha256",
            "source_locator",
        }
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError("unexpected evidence-record schema")
        name = record["filename"]
        if name in payloads or EXPECTED.get(name) != record["source_locator"]:
            raise ValueError("unexpected filename, duplicate, or source locator")
        if record["http_status"] != 200:
            raise ValueError(f"unsuccessful retrieval: {name}")
        if record["historical_availability_proven"] is not False:
            raise ValueError("current receipt cannot prove historical availability")
        started = _utc(record["retrieval_started_at_utc"], "retrieval start")
        completed = _utc(record["retrieval_completed_at_utc"], "retrieval completion")
        if completed < started:
            raise ValueError("retrieval completion precedes start")
        if completed > audited_at:
            raise ValueError("retrieval completion is after audit time")
        payload = (root / name).read_bytes()
        if record["bytes"] != len(payload) or record["sha256"] != _sha256(payload):
            raise ValueError(f"artifact integrity mismatch: {name}")
        payloads[name] = payload

    pdf = payloads["nyse_2026_calendar.pdf"]
    if not pdf.startswith(b"%PDF-") or b"%%EOF" not in pdf or b"/Type/Page" not in pdf:
        raise ValueError("NYSE yearly calendar PDF structure mismatch")

    page = _visible_html(payloads["nyse_hours_calendars.html"])
    anchors = {
        "All NYSE markets observe U.S. holidays as listed below for 2026, 2027, and 2028.",
        "Labor Day Monday, September 7",
        "Core Trading Session: 9:30 a.m. to 4:00 p.m. ET",
        "All times are Eastern Time.",
    }
    missing = sorted(anchor for anchor in anchors if anchor not in page)
    if missing:
        raise ValueError(f"NYSE schedule anchors missing: {missing}")

    return {
        "market": "NYSE / XNYS",
        "requested_first_us_shadow_date": "2026-09-14",
        "official_schedule_text_anchors_present": True,
        "audited_at": audited_at.isoformat(),
        "latest_receipt_at": max(
            _utc(record["retrieval_completed_at_utc"], "retrieval completion")
            for record in records
        ).isoformat(),
        "calendar_observation": (
            "General holiday-scope, Labor Day and core-hours text anchors are "
            "present; target-date exception coverage has not been verified"
        ),
        "target_date_session_status": "UNKNOWN",
        "published_core_hours": "09:30-16:00 ET",
        "canonical_us2_session_evidence": "NO_GO",
        "shadow_scoring": "NOT_READY",
        "watchlist_frozen_by_cutoff": "UNKNOWN",
        "limitations": [
            "these checks do not establish an exact per-date XNYS session assertion",
            "the published clocks are Eastern Time rather than exact UTC timestamps",
            "no approved review binds these receipts into a HistoricalEvidencePackage",
            "this package contains no candidate, information cutoff, or frozen watchlist",
            "current receipt time is not historical availability evidence",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_forward_session_truth(args.root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
