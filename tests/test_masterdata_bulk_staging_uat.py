"""Bulk Master Data contract: bounded, approval-gated staging, no direct writes."""
import pytest
from pydantic import ValidationError
from app.masterdata import BulkRequest,BulkRow

def test_bulk_rejects_over_100_rows_and_invalid_operations():
    row={"record_key":"A001","operation":"CREATE","payload":{"name":"UAT Agent"},"reason":"Controlled bulk import"}
    with pytest.raises(ValidationError):
        BulkRequest(domain="agent",rows=[row]*101)
    with pytest.raises(ValidationError):
        BulkRow(**{**row,"operation":"DELETE"})

def test_bulk_preview_rejects_duplicate_keys_without_master_mutation(monkeypatch):
    import app.masterdata as md
    class Conn:
        def execute(self,sql,params):
            return self
        def fetchone(self):
            return None
        def close(self):
            pass
    monkeypatch.setattr(md,"connect",lambda:Conn())
    monkeypatch.setattr(md,"authenticated_actor",lambda c,t:({"user_ref":"uat-maker"}, {"MASTER_DATA"}))
    monkeypatch.setattr(md,"can_make_roles",lambda roles:True)
    monkeypatch.setattr(md,"validate_payload",lambda *args:[])
    monkeypatch.setattr(md,"reference_errors",lambda *args:[])
    row={"record_key":"ABC","operation":"CREATE","payload":{},"reason":"Bulk test"}
    result=md.bulk_preview(BulkRequest(domain="agent",rows=[row,{**row,"record_key":"abc"}]),x_m3_session="synthetic-session")
    assert result["valid"]==1
    assert result["rejected"]==1
    assert "DUPLICATE_KEY_IN_FILE" in result["rows"][1]["errors"]

def test_bulk_submit_creates_pending_requests_only(monkeypatch):
    import app.masterdata as md
    monkeypatch.setattr(md,"bulk_preview",lambda *args,**kwargs:{"rejected":0})
    created=[]
    def fake_change(b,x_m3_session=None):
        created.append((b.operation,x_m3_session))
        return {"change_ref":"MDC-UAT","status":"PENDING"}
    monkeypatch.setattr(md,"create_change",fake_change)
    payload=BulkRequest(domain="agent",rows=[{"record_key":"A1","operation":"CREATE","payload":{},"reason":"Controlled staging"}])
    result=md.bulk_submit(payload,x_m3_session="synthetic-session")
    assert result["pending"]==1 and result["rejected"]==0
    assert created==[("CREATE","synthetic-session")]
    assert result["rows"][0]["status"]=="PENDING"
