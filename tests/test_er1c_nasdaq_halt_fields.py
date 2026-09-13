import hashlib, json, os
from datetime import datetime, timezone
import pytest
from tools.audit_er1c_nasdaq_halt_fields import PURPOSE, audit_halt_fields

AUDITED_AT = datetime(2026, 9, 13, 2, tzinfo=timezone.utc)
def _write(root):
    items = {"field_definitions.html": b"<html>Halt Date Date of initial halt being implemented. Halt Time Time of initial halt being implemented including milliseconds if applicable. Mkt Market Category Code: NASDAQ Non-NASDAQ Date Date which trading is resumed. Resumption Quote Time Time when quotations are scheduled to resume following a halt. Resumption Trade Time Time when trading is scheduled to resume following a halt. When an issue resumes quoting, the code will change.</html>", "trading_halts.html": b"<html>Halt times displayed are Eastern Time (ET).</html>"}
    loc = {"field_definitions.html": "https://www.nasdaqtrader.com/Trader.aspx?id=TradeHaltCodes", "trading_halts.html": "https://www.nasdaqtrader.com/Trader.aspx?id=TradeHalts"}
    rows=[]
    for name,payload in items.items():
        (root/name).write_bytes(payload); rows.append({"bytes":len(payload),"content_type":"text/html; charset=utf-8","http_status":200,"path":name,"receipt_completed_at":"2026-09-13T01:01:00+00:00","request_started_at":"2026-09-13T01:00:00+00:00","resolved_locator":loc[name],"sha256":hashlib.sha256(payload).hexdigest(),"source_locator":loc[name]})
    doc={"artifacts":rows,"created_at":"2026-09-13T01:02:00+00:00","purpose":PURPOSE,"schema_version":1}; _manifest(root,doc)
def _manifest(root,doc):
    raw=(json.dumps(doc,indent=2,sort_keys=True)+"\n").encode(); (root/"manifest.json").write_bytes(raw); (root/"manifest.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n")
@pytest.fixture
def package(tmp_path): _write(tmp_path); return tmp_path
def _rehash(root,name,payload):
    (root/name).write_bytes(payload); doc=json.loads((root/"manifest.json").read_bytes()); row=next(x for x in doc["artifacts"] if x["path"]==name); row.update(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest()); _manifest(root,doc)
def test_qualifies_without_admitting_status(package):
    result=audit_halt_fields(package,audited_at=AUDITED_AT); assert result["documentation_integrity"]=="PASS"; assert result["feed_observations_acquired"]==0; assert result["canonical_security_status"]==result["xnys_identity_binding"]==result["execution_resumption_proof"]=="NO_GO"; assert result["latest_receipt_at"] == "2026-09-13T01:01:00+00:00"
def test_rejects_tamper(package):
    (package/"field_definitions.html").write_bytes(b"tampered")
    with pytest.raises(ValueError,match="integrity mismatch"): audit_halt_fields(package,audited_at=AUDITED_AT)
def test_rejects_changed_scheduled_semantics(package):
    p=(package/"field_definitions.html").read_bytes().replace(b"scheduled to resume",b"did resume",1); _rehash(package,"field_definitions.html",p)
    with pytest.raises(ValueError,match="anchors missing"): audit_halt_fields(package,audited_at=AUDITED_AT)
def test_rejects_missing_timezone(package):
    _rehash(package,"trading_halts.html",b"<html>Current halts</html>")
    with pytest.raises(ValueError,match="time-zone"): audit_halt_fields(package,audited_at=AUDITED_AT)
def test_rejects_redirect(package):
    doc=json.loads((package/"manifest.json").read_bytes()); doc["artifacts"][0]["resolved_locator"]="https://example.test/"; _manifest(package,doc)
    with pytest.raises(ValueError,match="redirect"): audit_halt_fields(package,audited_at=AUDITED_AT)
def test_rejects_unbounded_inventory(package):
    (package/"feed.xml").write_text("not acquired")
    with pytest.raises(ValueError,match="inventory"): audit_halt_fields(package,audited_at=AUDITED_AT)
def test_rejects_undeclared_directory(package):
    (package/"undeclared").mkdir()
    with pytest.raises(ValueError,match="inventory"): audit_halt_fields(package,audited_at=AUDITED_AT)
def test_rejects_symlinked_package_root(package):
    link=package.parent/"package-link"; link.symlink_to(package, target_is_directory=True)
    with pytest.raises(ValueError,match="real directory"): audit_halt_fields(link,audited_at=AUDITED_AT)
@pytest.mark.parametrize("name", ["manifest.json", "manifest.sha256", "field_definitions.html", "trading_halts.html"])
def test_rejects_symlinked_package_entry(package,name):
    target=package.parent/f"{package.name}-{name}.target"; target.write_bytes((package/name).read_bytes()); (package/name).unlink(); (package/name).symlink_to(target)
    with pytest.raises(ValueError,match="regular non-symlink"): audit_halt_fields(package,audited_at=AUDITED_AT)
@pytest.mark.parametrize("name", ["manifest.json", "manifest.sha256", "field_definitions.html", "trading_halts.html"])
def test_rejects_hard_linked_package_entry(package,name):
    target=package.parent/f"{package.name}-{name}.target"; os.link(package/name,target)
    with pytest.raises(ValueError,match="hard linked"): audit_halt_fields(package,audited_at=AUDITED_AT)
def test_rejects_non_utc_receipt(package):
    doc=json.loads((package/"manifest.json").read_bytes()); doc["artifacts"][0]["receipt_completed_at"]="2026-09-13T03:00:00"; _manifest(package,doc)
    with pytest.raises(ValueError,match="explicit UTC"): audit_halt_fields(package,audited_at=AUDITED_AT)
