"""Disposable SQLite bulk maker/checker lifecycle. IAM identity is a test fixture,
not a claim that live authenticated UAT has passed. No external database touched.
"""
import json
import sqlite3
import pytest
from fastapi import HTTPException
from app import masterdata as md

def test_bulk_pending_four_eyes_approval_and_audit(tmp_path, monkeypatch):
    path=tmp_path/"bulk-uat.sqlite3"
    def connect():
        c=sqlite3.connect(str(path),isolation_level=None)
        c.row_factory=sqlite3.Row
        return c
    c=connect()
    md.init_schema(c)
    c.close()
    monkeypatch.setattr(md,"connect",connect)
    identities={
        "maker-session":({"user_ref":"maker-001","username":"md.maker"},{"MASTER_DATA"}),
        "checker-session":({"user_ref":"checker-002","username":"md.checker"},{"MASTER_DATA_MANAGER"}),
        "viewer-session":({"user_ref":"viewer-003","username":"viewer"},{"VIEWER"}),
    }
    def auth(c,token):
        if token not in identities:raise HTTPException(401,{"code":"INVALID_SESSION"})
        return identities[token]
    monkeypatch.setattr(md,"authenticated_actor",auth)
    monkeypatch.setattr(md,"sync_operational_projection",lambda *args:None)
    request=md.BulkRequest(domain="agent",rows=[{
        "record_key":"UAT-AG001","operation":"CREATE",
        "payload":{"name":"Disposable UAT Agent"},"reason":"Isolated maker checker test"
    }])
    with pytest.raises(HTTPException) as exc:
        md.bulk_preview(request,x_m3_session="viewer-session")
    assert exc.value.status_code==403
    preview=md.bulk_preview(request,x_m3_session="maker-session")
    assert preview["valid"]==1 and preview["rejected"]==0
    outcome=md.bulk_submit(request,x_m3_session="maker-session")
    assert outcome["pending"]==1 and outcome["rejected"]==0
    ref=outcome["rows"][0]["change_ref"]
    c=connect()
    assert c.execute("SELECT COUNT(*) FROM md_records").fetchone()[0]==0
    assert c.execute("SELECT status,maker FROM md_change_requests WHERE change_ref=?",(ref,)).fetchone()["status"]=="PENDING"
    c.close()
    with pytest.raises(HTTPException) as exc:
        md.decide(ref,md.Decision(decision="APPROVE"),x_m3_session="maker-session")
    assert exc.value.status_code in (403,409)
    decision=md.decide(ref,md.Decision(decision="APPROVE"),x_m3_session="checker-session")
    assert decision["status"]=="APPROVED" and decision["version"]==1
    c=connect()
    row=c.execute("SELECT payload_json,approved_by FROM md_records WHERE domain='agent' AND record_key='UAT-AG001'").fetchone()
    assert json.loads(row["payload_json"])["name"]=="Disposable UAT Agent"
    assert row["approved_by"]=="checker-002"
    assert c.execute("SELECT COUNT(*) FROM md_versions WHERE record_key='UAT-AG001'").fetchone()[0]==1
    events=[r[0] for r in c.execute("SELECT action FROM md_audit WHERE record_key='UAT-AG001'")]
    assert "CHANGE_REQUEST_CREATE" in events
    assert "CHANGE_APPROVE_APPLY" in events
    c.close()
    with pytest.raises(HTTPException) as exc:
        md.decide(ref,md.Decision(decision="APPROVE"),x_m3_session="checker-session")
    assert exc.value.status_code==409
