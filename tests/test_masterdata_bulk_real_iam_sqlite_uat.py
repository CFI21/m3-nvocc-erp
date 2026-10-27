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
      CREATE TABLE iam_users(id INTEGER PRIMARY KEY,user_ref TEXT,username TEXT,home_office_id INTEGER);
      CREATE TABLE iam_sessions(id INTEGER PRIMARY KEY,user_id INTEGER,session_token TEXT,status TEXT,
                                 revoked_at TEXT,expires_at TEXT,last_seen_at TEXT);
      CREATE TABLE iam_roles(id INTEGER PRIMARY KEY,role_code TEXT);
      CREATE TABLE iam_user_roles(id INTEGER PRIMARY KEY,user_id INTEGER,role_id INTEGER,office_id INTEGER,
                                   country_id INTEGER,organization_id INTEGER,status TEXT,valid_to TEXT);
    """)
    c.execute("INSERT INTO iam_offices VALUES(1,'UAT')")
    c.execute("INSERT INTO iam_users VALUES(1,'maker-1','md.maker',1)")
    c.execute("INSERT INTO iam_users VALUES(2,'viewer-2','viewer',1)")
    c.execute("INSERT INTO iam_roles VALUES(1,'MASTER_DATA')")
    c.execute("INSERT INTO iam_roles VALUES(2,'VIEWER')")
    c.execute("INSERT INTO iam_user_roles VALUES(1,1,1,1,NULL,NULL,'ACTIVE',NULL)")
    c.execute("INSERT INTO iam_user_roles VALUES(2,2,2,1,NULL,NULL,'ACTIVE',NULL)")
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
