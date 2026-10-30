"""Use the actual IAM session/role lookup in disposable SQLite for bulk preview.

This is an isolated automated IAM regression, not proof of live browser UAT.
"""
import datetime
import sqlite3
import pytest
from fastapi import HTTPException
from app import admin, masterdata as md

def test_bulk_preview_requires_valid_active_iam_session_and_maker_role(tmp_path,monkeypatch):
    db=tmp_path/"iam-bulk.sqlite3"
    def connect():
        c=sqlite3.connect(db,isolation_level=None)
        c.row_factory=sqlite3.Row
        return c
    c=connect()
    c.executescript("""
      CREATE TABLE iam_offices(id INTEGER PRIMARY KEY,office_code TEXT);
      CREATE TABLE iam_users(id INTEGER PRIMARY KEY,user_ref TEXT,username TEXT,home_office_id INTEGER,status TEXT NOT NULL DEFAULT 'ACTIVE');
      CREATE TABLE iam_sessions(id INTEGER PRIMARY KEY,user_id INTEGER,session_token TEXT,status TEXT,
                                 revoked_at TEXT,expires_at TEXT,last_seen_at TEXT);
      CREATE TABLE iam_roles(id INTEGER PRIMARY KEY,role_code TEXT,status TEXT NOT NULL DEFAULT 'ACTIVE');
      CREATE TABLE iam_user_roles(id INTEGER PRIMARY KEY,user_id INTEGER,role_id INTEGER,office_id INTEGER,
                                   country_id INTEGER,organization_id INTEGER,status TEXT,valid_to TEXT,valid_from TEXT NOT NULL DEFAULT '2000-01-01T00:00:00+00:00');
    """)
    c.execute("INSERT INTO iam_offices VALUES(1,'UAT')")
    c.execute("INSERT INTO iam_users(id,user_ref,username,home_office_id) VALUES(1,'maker-1','md.maker',1)")
    c.execute("INSERT INTO iam_users(id,user_ref,username,home_office_id) VALUES(2,'viewer-2','viewer',1)")
    c.execute("INSERT INTO iam_roles(id,role_code) VALUES(1,'MASTER_DATA')")
    c.execute("INSERT INTO iam_roles(id,role_code) VALUES(2,'VIEWER')")
    c.execute("INSERT INTO iam_user_roles(id,user_id,role_id,office_id,country_id,organization_id,status,valid_to) VALUES(1,1,1,1,NULL,NULL,'ACTIVE',NULL)")
    c.execute("INSERT INTO iam_user_roles(id,user_id,role_id,office_id,country_id,organization_id,status,valid_to) VALUES(2,2,2,1,NULL,NULL,'ACTIVE',NULL)")
    future=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=2)).isoformat()
    past=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=2)).isoformat()
    c.execute("INSERT INTO iam_sessions VALUES(1,1,'maker-good','ACTIVE',NULL,?,NULL)",(future,))
    c.execute("INSERT INTO iam_sessions VALUES(2,1,'maker-expired','ACTIVE',NULL,?,NULL)",(past,))
    c.execute("INSERT INTO iam_sessions VALUES(3,2,'viewer-good','ACTIVE',NULL,?,NULL)",(future,))
    c.close()
    monkeypatch.setattr(md,"connect",connect)
    # The actual md.authenticated_actor -> admin.session -> admin.roles_for
    # path is deliberately left UNPATCHED.
    request=md.BulkRequest(domain="agent",rows=[{
      "record_key":"TEST-1","operation":"CREATE",
      "payload":{"name":"Disposable Agent"},"reason":"Test-only read"
    }])
    for token,expected in [(None,401),("bogus",401),("maker-expired",401),("viewer-good",403)]:
        with pytest.raises(HTTPException) as caught:
            md.bulk_preview(request,x_m3_session=token)
        assert caught.value.status_code==expected
    # Before reaching normal master validation, a verified maker must pass IAM.
    c=connect()
    session,roles=md.authenticated_actor(c,"maker-good")
    assert session["user_ref"]=="maker-1"
    assert roles=={"MASTER_DATA"}
    assert md.can_make_roles(roles)
    c.close()


def test_bulk_full_lifecycle_with_real_iam_sessions(tmp_path,monkeypatch):
    """Real IAM session/role lookup, synthetic business data, disposable SQLite only."""
    import json
    db=tmp_path/"iam-bulk-lifecycle.sqlite3"
    def connect():
        c=sqlite3.connect(db,isolation_level=None)
        c.row_factory=sqlite3.Row
        return c
    c=connect()
    md.init_schema(c)
    c.executescript("""
      CREATE TABLE iam_offices(id INTEGER PRIMARY KEY,office_code TEXT);
      CREATE TABLE iam_users(id INTEGER PRIMARY KEY,user_ref TEXT,username TEXT,home_office_id INTEGER,status TEXT NOT NULL DEFAULT 'ACTIVE');
      CREATE TABLE iam_sessions(id INTEGER PRIMARY KEY,user_id INTEGER,session_token TEXT,status TEXT,
                                 revoked_at TEXT,expires_at TEXT,last_seen_at TEXT);
      CREATE TABLE iam_roles(id INTEGER PRIMARY KEY,role_code TEXT,status TEXT NOT NULL DEFAULT 'ACTIVE');
      CREATE TABLE iam_user_roles(id INTEGER PRIMARY KEY,user_id INTEGER,role_id INTEGER,office_id INTEGER,
                                   country_id INTEGER,organization_id INTEGER,status TEXT,valid_to TEXT,valid_from TEXT NOT NULL DEFAULT '2000-01-01T00:00:00+00:00');
    """)
    c.executemany("INSERT INTO iam_offices VALUES(?,?)",[(1,"UAT")])
    c.executemany("INSERT INTO iam_users(id,user_ref,username,home_office_id) VALUES(?,?,?,?)",[
        (1,"maker-001","md.maker",1),(2,"checker-002","md.checker",1)])
    c.executemany("INSERT INTO iam_roles(id,role_code) VALUES(?,?)",[
        (1,"MASTER_DATA"),(2,"MASTER_DATA_MANAGER")])
    c.executemany("INSERT INTO iam_user_roles(id,user_id,role_id,office_id,country_id,organization_id,status,valid_to) VALUES(?,?,?,?,?,?,?,?)",[
        (1,1,1,1,None,None,"ACTIVE",None),
        (2,2,2,1,None,None,"ACTIVE",None)])
    future=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=2)).isoformat()
    c.executemany("INSERT INTO iam_sessions VALUES(?,?,?,?,?,?,?)",[
        (1,1,"maker-valid","ACTIVE",None,future,None),
        (2,2,"checker-valid","ACTIVE",None,future,None)])
    c.close()
    monkeypatch.setattr(md,"connect",connect)
    monkeypatch.setattr(md,"sync_operational_projection",lambda *args:None)
    request=md.BulkRequest(domain="agent",rows=[{
        "record_key":"IAM-UAT-001","operation":"CREATE",
        "payload":{"name":"Synthetic IAM Agent"},"reason":"Disposable IAM UAT"
    }])
    # All three bulk entry points must reject invalid sessions before parsing or mutation.
    for token in (None,"invalid-session"):
        with pytest.raises(HTTPException) as denied:
            md.bulk_submit(request,x_m3_session=token)
        assert denied.value.status_code==401
        with pytest.raises(HTTPException) as denied:
            md.bulk_parse_xlsx(md.BulkXlsxInput(content_base64="YWJj"),x_m3_session=token)
        assert denied.value.status_code==401
    c=connect()
    assert c.execute("SELECT COUNT(*) FROM md_change_requests").fetchone()[0]==0
    c.close()
    preview=md.bulk_preview(request,x_m3_session="maker-valid")
    assert preview["valid"]==1 and preview["rejected"]==0
    submitted=md.bulk_submit(request,x_m3_session="maker-valid")
    assert submitted["pending"]==1 and submitted["rejected"]==0
    ref=submitted["rows"][0]["change_ref"]
    c=connect()
    assert c.execute("SELECT COUNT(*) FROM md_records").fetchone()[0]==0
    assert c.execute("SELECT status FROM md_change_requests WHERE change_ref=?",(ref,)).fetchone()["status"]=="PENDING"
    c.close()
    with pytest.raises(HTTPException) as exc:
        md.decide(ref,md.Decision(decision="APPROVE"),x_m3_session="maker-valid")
    assert exc.value.status_code in (403,409)
    result=md.decide(ref,md.Decision(decision="APPROVE"),x_m3_session="checker-valid")
    assert result["status"]=="APPROVED"
    c=connect()
    record=c.execute("SELECT payload_json,approved_by FROM md_records WHERE domain='agent' AND record_key='IAM-UAT-001'").fetchone()
    assert json.loads(record["payload_json"])["name"]=="Synthetic IAM Agent"
    assert record["approved_by"]=="checker-002"
    assert c.execute("SELECT COUNT(*) FROM md_versions WHERE record_key='IAM-UAT-001'").fetchone()[0]==1
    actions={r["action"] for r in c.execute("SELECT action FROM md_audit WHERE record_key='IAM-UAT-001'")}
    assert {"CHANGE_REQUEST_CREATE","CHANGE_APPROVE_APPLY"}<=actions
    c.close()
