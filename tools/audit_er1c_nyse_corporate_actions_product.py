"""Audit NYSE product documentation without treating it as action-event data."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path


PRODUCT_SOURCE = "https://www.nyse.com/market-data/corporate-actions"
SPEC_SOURCE = (
    "https://www.nyse.com/publicdocs/nyse/data/"
    "Distribution_NYSE_CorporateActions_Client_Specification.v2.2.6.pdf"
)
PURPOSE = (
    "Current official NYSE corporate-actions product/schema qualification only; "
    "no historical event coverage or PIT admission"
)
TOP_FIELDS = {
    "bytes", "filename", "historical_availability_proven", "http_status",
    "product_page", "purpose", "resolved_locator", "retrieval_completed_at_utc",
    "retrieval_started_at_utc", "schema", "sha256", "source_locator",
}
ARTIFACT_FIELDS = {
    "bytes", "filename", "historical_availability_proven", "http_status",
    "resolved_locator", "retrieval_completed_at_utc", "retrieval_started_at_utc",
    "sha256", "source_locator",
}
SPEC_BINARY_ANCHORS = (
    b"/Type/Pages/Count 24",
    b"/CreationDate(D:20240523134530-04'00')",
    b"mailto:NYSEDataMail@nyse.com",
    b"https://www.nyse.com/publicdocs/nyse/data/NYSE_SFTP_Access_User_Guide.pdf",
    b"https://www.nyse.com/publicdocs/nyse/data/NYSE_TAQ_Data_AWS_Cloud_Access_Dev_Instructions.pdf",
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} is not an explicit UTC timestamp")
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def _check_receipt(doc: dict, *, label: str, audited_at: datetime) -> None:
    if doc["http_status"] != 200:
        raise ValueError(f"{label} retrieval was unsuccessful")
    if doc["historical_availability_proven"] is not False:
        raise ValueError("current receipt cannot prove historical availability")
    started = _utc(doc["retrieval_started_at_utc"], f"{label} retrieval start")
    completed = _utc(doc["retrieval_completed_at_utc"], f"{label} retrieval completion")
    if completed < started:
        raise ValueError(f"{label} retrieval completion precedes start")
    if completed > audited_at:
        raise ValueError(f"{label} retrieval completion is after audit time")


def _visible_text(payload: bytes) -> str:
    try:
        source = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("NYSE product page is not UTF-8") from error
    if "<html" not in source.lower():
        raise ValueError("NYSE product artifact is not HTML")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", source)).split())


def audit_product(root: Path, *, audited_at: datetime | None = None) -> dict:
    if audited_at is None:
        audited_at = datetime.now(timezone.utc)
    if type(audited_at) is not datetime or audited_at.tzinfo is not timezone.utc:
        raise ValueError("audited_at must use datetime.timezone.utc")

    raw_manifest = (root / "manifest.json").read_bytes()
    if (root / "manifest.sha256").read_text(encoding="ascii").strip() != _sha256(raw_manifest):
        raise ValueError("manifest SHA256 sidecar mismatch")
    manifest = json.loads(raw_manifest)
    if not isinstance(manifest, dict) or set(manifest) != TOP_FIELDS:
        raise ValueError("unexpected manifest fields")
    if manifest["schema"] != "er1c-nyse-corporate-actions-client-spec-v1":
        raise ValueError("unexpected manifest schema")
    if manifest["purpose"] != PURPOSE:
        raise ValueError("unexpected evidence purpose")
    if set(manifest["product_page"]) != ARTIFACT_FIELDS:
        raise ValueError("unexpected product-page manifest fields")

    spec = manifest
    page = manifest["product_page"]
    expected = {
        "filename": "nyse_corporate_actions_client_spec_v2.2.6.pdf",
        "source_locator": SPEC_SOURCE,
        "resolved_locator": SPEC_SOURCE,
    }
    page_expected = {
        "filename": "nyse_corporate_actions_product.html",
        "source_locator": PRODUCT_SOURCE,
        "resolved_locator": PRODUCT_SOURCE,
    }
    if any(spec[key] != value for key, value in expected.items()):
        raise ValueError("unexpected specification identity")
    if any(page[key] != value for key, value in page_expected.items()):
        raise ValueError("unexpected product-page identity")
    _check_receipt(spec, label="specification", audited_at=audited_at)
    _check_receipt(page, label="product page", audited_at=audited_at)

    inventory = {item.name for item in root.iterdir() if item.is_file()}
    if inventory != {
        "manifest.json", "manifest.sha256", spec["filename"], page["filename"]
    }:
        raise ValueError("evidence inventory does not match manifest scope")
    spec_bytes = (root / spec["filename"]).read_bytes()
    page_bytes = (root / page["filename"]).read_bytes()
    for doc, payload, label in ((spec, spec_bytes, "specification"), (page, page_bytes, "product page")):
        if doc["bytes"] != len(payload) or doc["sha256"] != _sha256(payload):
            raise ValueError(f"{label} integrity mismatch")
    if not spec_bytes.startswith(b"%PDF-"):
        raise ValueError("specification artifact is not a PDF")
    missing_spec_anchors = [
        anchor.decode("ascii") for anchor in SPEC_BINARY_ANCHORS
        if anchor not in spec_bytes
    ]
    if missing_spec_anchors:
        raise ValueError(
            f"specification identity anchors missing: {missing_spec_anchors}"
        )

    text = _visible_text(page_bytes)
    anchors = {
        "Market Event Feed is a real-time API that provides programmatic access to Corporate Actions",
        "over 60 different corporate actions types for all equities listed on NYSE Group",
        "cash dividends, stock dividends, distributions, splits, new listings (IPOs), suspensions and delistings",
        "consolidated view of daily corporate events happening on the NYSE Group",
        "on the current Trading Day",
    }
    missing = sorted(anchor for anchor in anchors if anchor not in text)
    if missing:
        raise ValueError(f"product-scope anchors missing: {missing}")

    return {
        "source_classification": "AUTHORITATIVE_WITHIN_PRODUCT_DOCUMENTATION_SCOPE",
        "documented_event_scope": "OVER_60_TYPES_ACROSS_NYSE_GROUP",
        "event_files_acquired": False,
        "historical_archive_access_proven_free": False,
        "historical_availability": "UNPROVEN",
        "complete_bounded_us4_action_coverage": "NO_GO",
        "canonical_pit_admission": "NO_GO",
        "limitations": [
            "product and schema documentation are not corporate-action event records",
            "the public product page describes current-day and programmatic products but does not provide the pilot event inventory",
            "no complete 2022 event files or explicit empty coverage were acquired",
            "current receipt time cannot be represented as historical availability",
            "documentation of many action types does not establish completeness for IBM or TWTR",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_product(args.root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
