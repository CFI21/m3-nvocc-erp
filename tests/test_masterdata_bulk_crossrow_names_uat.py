"""Cross-row duplicate-name safety for master-data bulk staging."""
from app import masterdata as md

class FakeConn:
    def execute(self, sql, params): return self
    def fetchone(self): return None
    def close(self): pass

def test_bulk_preview_rejects_same_display_name_with_different_keys(monkeypatch):
    monkeypatch.setattr(md, "connect", lambda: FakeConn())
    monkeypatch.setattr(md, "authenticated_actor", lambda c,t: ({"user_ref":"maker"}, {"MASTER_DATA"}))
    monkeypatch.setattr(md, "can_make_roles", lambda roles: True)
    monkeypatch.setattr(md, "validate_payload", lambda *args: [])
    monkeypatch.setattr(md, "reference_errors", lambda *args: [])
    monkeypatch.setattr(md, "duplicate_name", lambda *args: None)
    monkeypatch.setattr(md, "pending_duplicate_name", lambda *args: None)
    rows=[
        {"record_key":"AG-001","operation":"CREATE","payload":{"name":"North Star Logistics"},"reason":"UAT fixture"},
        {"record_key":"AG-002","operation":"CREATE","payload":{"name":"  north star logistics  "},"reason":"UAT fixture"}
    ]
    preview=md.bulk_preview(md.BulkRequest(domain="agent",rows=rows),x_m3_session="synthetic-session")
    assert preview["valid"]==1
    assert preview["rejected"]==1
    assert "DUPLICATE_DISPLAY_NAME_IN_FILE" in preview["rows"][1]["errors"]

def test_duplicate_display_name_prevents_batch_submission(monkeypatch):
    monkeypatch.setattr(md, "connect", lambda: FakeConn())
    monkeypatch.setattr(md, "authenticated_actor", lambda c,t: ({"user_ref":"maker"}, {"MASTER_DATA"}))
    monkeypatch.setattr(md, "can_make_roles", lambda roles: True)
    monkeypatch.setattr(md, "validate_payload", lambda *args: [])
    monkeypatch.setattr(md, "reference_errors", lambda *args: [])
    monkeypatch.setattr(md, "duplicate_name", lambda *args: None)
    monkeypatch.setattr(md, "pending_duplicate_name", lambda *args: None)
    def no_write(*args,**kwargs):raise AssertionError("Must not stage invalid batch")
    monkeypatch.setattr(md, "create_change", no_write)
    rows=[
        {"record_key":"C1","operation":"CREATE","payload":{"name":"Duplicate Customer"},"reason":"UAT fixture"},
        {"record_key":"C2","operation":"CREATE","payload":{"name":"duplicate customer"},"reason":"UAT fixture"}
    ]
    from fastapi import HTTPException
    try:
        md.bulk_submit(md.BulkRequest(domain="customer",rows=rows),x_m3_session="synthetic-session")
        assert False, "Rejected batch must not submit"
    except HTTPException as e:
        assert e.status_code==422
        assert e.detail["code"]=="BULK_PREVIEW_REJECTED"

def test_bulk_preview_rejects_collapsed_internal_whitespace_duplicate(monkeypatch):
    monkeypatch.setattr(md, "connect", lambda: FakeConn())
    monkeypatch.setattr(md, "authenticated_actor", lambda c,t: ({"user_ref":"maker"}, {"MASTER_DATA"}))
    monkeypatch.setattr(md, "validate_payload", lambda *args: [])
    monkeypatch.setattr(md, "reference_errors", lambda *args: [])
    monkeypatch.setattr(md, "duplicate_name", lambda *args: None)
    monkeypatch.setattr(md, "pending_duplicate_name", lambda *args: None)
    rows = [
        {"record_key":"EQ-001","operation":"CREATE","payload":{"name":"High Cube"},"reason":"Synthetic fixture"},
        {"record_key":"EQ-002","operation":"CREATE","payload":{"name":"high   cube"},"reason":"Synthetic fixture"},
    ]
    request=md.BulkRequest(domain="equipment-type",rows=rows)
    preview=md.bulk_preview(request,x_m3_session="synthetic-session")
    assert preview["rejected"] == 1
    assert "DUPLICATE_DISPLAY_NAME_IN_FILE" in preview["rows"][1]["errors"]
