"""Audit retained Nasdaq halt-field documentation without admitting halt facts."""
from __future__ import annotations
import argparse, hashlib, html, json, re
from datetime import datetime, timezone
from pathlib import Path

EXPECTED = {"field_definitions.html": "https://www.nasdaqtrader.com/Trader.aspx?id=TradeHaltCodes", "trading_halts.html": "https://www.nasdaqtrader.com/Trader.aspx?id=TradeHalts"}
PURPOSE = "Documentation-only qualification of official Nasdaq trade-halt RSS field definitions and trading-halts reference; no feed observations or security status evidence."

def _sha(payload: bytes) -> str: return hashlib.sha256(payload).hexdigest()
def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str): raise ValueError(f"{label} is not a timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None): raise ValueError(f"{label} is not explicit UTC")
    return parsed.astimezone(timezone.utc)
def _visible(payload: bytes) -> str:
    try: source = payload.decode("utf-8")
    except UnicodeDecodeError as error: raise ValueError("artifact is not UTF-8") from error
    if "<html" not in source.lower(): raise ValueError("artifact is not HTML")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", source)).split())

def audit_halt_fields(root: Path, *, audited_at: datetime | None = None) -> dict:
    audited_at = audited_at or datetime.now(timezone.utc)
    if type(audited_at) is not datetime or audited_at.tzinfo is None: raise ValueError("audited_at must be timezone-aware")
    audited_at = audited_at.astimezone(timezone.utc)
    raw = (root / "manifest.json").read_bytes()
    if (root / "manifest.sha256").read_text(encoding="ascii").strip() != f"{_sha(raw)}  manifest.json": raise ValueError("manifest SHA256 sidecar mismatch")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or set(manifest) != {"artifacts", "created_at", "purpose", "schema_version"}: raise ValueError("unexpected manifest fields")
    if manifest["schema_version"] != 1 or manifest["purpose"] != PURPOSE: raise ValueError("unexpected manifest scope")
    created = _utc(manifest["created_at"], "manifest creation")
    if created > audited_at: raise ValueError("manifest creation is after audit time")
    records = manifest["artifacts"]
    if not isinstance(records, list) or len(records) != 2: raise ValueError("unexpected artifact records")
    if {p.name for p in root.iterdir() if p.is_file()} != set(EXPECTED) | {"manifest.json", "manifest.sha256"}: raise ValueError("artifact inventory does not match bounded scope")
    required = {"bytes", "content_type", "http_status", "path", "receipt_completed_at", "request_started_at", "resolved_locator", "sha256", "source_locator"}
    payloads: dict[str, bytes] = {}
    receipt_times: list[datetime] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != required: raise ValueError("unexpected artifact-record schema")
        name = record["path"]
        if name in payloads or EXPECTED.get(name) != record["source_locator"]: raise ValueError("unexpected path, duplicate, or source locator")
        if record["resolved_locator"] != record["source_locator"]: raise ValueError("unexpected redirect or resolved locator")
        if record["http_status"] != 200 or not str(record["content_type"]).lower().startswith("text/html"): raise ValueError("artifact retrieval or media type mismatch")
        started, completed = _utc(record["request_started_at"], "request start"), _utc(record["receipt_completed_at"], "receipt completion")
        if completed < started or completed > created or completed > audited_at: raise ValueError("invalid artifact receipt ordering")
        payload = (root / name).read_bytes()
        if record["bytes"] != len(payload) or record["sha256"] != _sha(payload): raise ValueError(f"artifact integrity mismatch: {name}")
        payloads[name] = payload
        receipt_times.append(completed)
    fields = _visible(payloads["field_definitions.html"])
    anchors = {"Halt Date Date of initial halt being implemented.", "Halt Time Time of initial halt being implemented including milliseconds if applicable.", "Mkt Market Category Code: NASDAQ Non-NASDAQ", "Date Date which trading is resumed.", "Resumption Quote Time Time when quotations are scheduled to resume following a halt.", "Resumption Trade Time Time when trading is scheduled to resume following a halt.", "When an issue resumes quoting, the code will change."}
    missing = sorted(a for a in anchors if a not in fields)
    if missing: raise ValueError(f"halt-field anchors missing: {missing}")
    if "Halt times displayed are Eastern Time (ET)." not in _visible(payloads["trading_halts.html"]): raise ValueError("halt time-zone anchor missing")
    return {"source": "Nasdaq Trader", "latest_receipt_at": max(receipt_times).isoformat(), "documentation_integrity": "PASS", "feed_observations_acquired": 0, "field_semantics": {"halt_clock": "initial halt; displayed in Eastern Time", "resumption_date": "date trading is resumed", "quote_resumption_clock": "scheduled quotation resume", "trade_resumption_clock": "scheduled trading resume", "market_category": "NASDAQ or Non-NASDAQ only"}, "canonical_security_status": "NO_GO", "xnys_identity_binding": "NO_GO", "execution_resumption_proof": "NO_GO", "limitations": ["documentation contains no issue-specific feed observation", "scheduled resumption times do not prove actual quoting or execution", "Non-NASDAQ does not identify XNYS or bind a stable security identity", "current receipts do not prove historical availability", "absence from a query cannot clear an unresolved prior halt"]}

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("root", type=Path); args = parser.parse_args()
    print(json.dumps(audit_halt_fields(args.root), indent=2, sort_keys=True))
if __name__ == "__main__": main()
