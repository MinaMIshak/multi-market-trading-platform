"""Audit a current NYSE page without promoting it to historical action truth."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path


SOURCE = "https://www.nyse.com/regulation/corporate-actions-market-watch-proxy-compliance"
PURPOSE = (
    "Current official corporate-action notice-scope qualification only; no complete "
    "historical action coverage or PIT admission"
)
FIELDS = {
    "bytes", "filename", "historical_availability_proven", "http_status",
    "purpose", "resolved_locator", "retrieval_completed_at_utc",
    "retrieval_started_at_utc", "schema", "sha256", "source_locator",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} is not an explicit UTC timestamp")
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def _visible_text(payload: bytes) -> str:
    try:
        source = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("NYSE artifact is not UTF-8") from error
    if "<html" not in source.lower():
        raise ValueError("NYSE artifact is not HTML")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", source)).split())


def audit_scope(root: Path, *, audited_at: datetime | None = None) -> dict:
    if audited_at is None:
        audited_at = datetime.now(timezone.utc)
    if type(audited_at) is not datetime or audited_at.tzinfo is not timezone.utc:
        raise ValueError("audited_at must use datetime.timezone.utc")

    raw_manifest = (root / "manifest.json").read_bytes()
    sidecar = (root / "manifest.sha256").read_text(encoding="ascii").strip()
    if sidecar != _sha256(raw_manifest):
        raise ValueError("manifest SHA256 sidecar mismatch")
    manifest = json.loads(raw_manifest)
    if not isinstance(manifest, dict) or set(manifest) != FIELDS:
        raise ValueError("unexpected manifest fields")
    if manifest["schema"] != "er1c-nyse-corporate-actions-scope-v1":
        raise ValueError("unexpected manifest schema")
    if manifest["purpose"] != PURPOSE:
        raise ValueError("unexpected evidence purpose")
    if manifest["filename"] != "nyse_corporate_actions.html":
        raise ValueError("unexpected artifact filename")
    if manifest["source_locator"] != SOURCE or manifest["resolved_locator"] != SOURCE:
        raise ValueError("unexpected source locator")
    if manifest["http_status"] != 200:
        raise ValueError("unsuccessful retrieval")
    if manifest["historical_availability_proven"] is not False:
        raise ValueError("current receipt cannot prove historical availability")

    started = _utc(manifest["retrieval_started_at_utc"], "retrieval start")
    completed = _utc(manifest["retrieval_completed_at_utc"], "retrieval completion")
    if completed < started:
        raise ValueError("retrieval completion precedes start")
    if completed > audited_at:
        raise ValueError("retrieval completion is after audit time")

    expected_inventory = {
        "manifest.json", "manifest.sha256", "nyse_corporate_actions.html"
    }
    actual_inventory = {item.name for item in root.iterdir() if item.is_file()}
    if actual_inventory != expected_inventory:
        raise ValueError("evidence inventory does not match manifest scope")
    payload = (root / manifest["filename"]).read_bytes()
    if manifest["bytes"] != len(payload) or manifest["sha256"] != _sha256(payload):
        raise ValueError("artifact integrity mismatch")

    text = _visible_text(payload)
    anchors = {
        "Advance notice to the Exchange and public dissemination via a press release",
        "change in name/symbol/CUSIP, reverse split, redomestication, business reorganization",
        "at least ten calendar days in advance of the effective date of the corporate action",
        "The Exchange publishes upcoming corporate action events on a daily basis.",
        "issuer name and symbol, anticipated date, corporate action type, and status",
        "info notices, which are available through a Market Data subscription",
    }
    missing = sorted(anchor for anchor in anchors if anchor not in text)
    if missing:
        raise ValueError(f"corporate-action scope anchors missing: {missing}")

    return {
        "source_classification": "AUTHORITATIVE_WITHIN_CURRENT_PUBLICATION_POLICY_SCOPE",
        "official_scope_anchors_present": True,
        "current_list_scope": "UPCOMING_EVENTS_DAILY",
        "event_specific_info_notice_access": "MARKET_DATA_SUBSCRIPTION",
        "historical_availability": "UNPROVEN",
        "complete_bounded_us4_action_coverage": "NO_GO",
        "canonical_pit_admission": "NO_GO",
        "limitations": [
            "the page describes issuer notice obligations and current publication policy",
            "it does not expose a complete free historical archive for the pilot interval",
            "notice obligations do not prove that every required action category is empty or complete",
            "current receipt time cannot be represented as historical availability",
            "subscription-only info notices were not acquired under the zero-budget constraint",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_scope(args.root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
