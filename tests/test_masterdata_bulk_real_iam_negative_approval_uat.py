"""Real IAM revoked/expired-session and role-revocation guard for bulk approvals.

This is an automated isolated SQLite regression, NOT authenticated browser UAT.
No Render, Neon, production, or real business records are modified.
"""
import datetime
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app import masterdata as md


@pytest.mark.parametrize("domain", ("agent", "customer", "equipment-type", "container-type"))
def test_revoked_expired_checker_and_role_cannot_approve_pending_bulk(
    domain, tmp_path, monkeypatch
):
    db = tmp_path / "real-iam-negative.sqlite3"

    def connect():
        c = sqlite3.connect(str(db), isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    c = connect()
    md.init_schema(c)
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
    c.executemany("INSERT INTO iam_users VALUES(?,?,?,?)", [
        (1, "synthetic-maker", "md.maker", 1),
        (2, "synthetic-checker", "md.checker", 1),
    ])
    c.executemany("INSERT INTO iam_roles VALUES(?,?)", [
        (1, "MASTER_DATA"), (2, "MASTER_DATA_MANAGER")
    ])
    c.executemany("INSERT INTO iam_user_roles VALUES(?,?,?,?,?,?,?,?)", [
        (1, 1, 1, 1, None, None, "ACTIVE", None),
        (2, 2, 2, 1, None, None, "ACTIVE", None),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    future = (now + datetime.timedelta(hours=2)).isoformat()
    past = (now - datetime.timedelta(hours=2)).isoformat()
    c.executemany("INSERT INTO iam_sessions VALUES(?,?,?,?,?,?,?)", [
        (1, 1, "maker-active", "ACTIVE", None, future, None),
        (2, 1, "maker-expired", "ACTIVE", None, past, None),
        (3, 1, "maker-revoked", "REVOKED", now.isoformat(), future, None),
        (4, 2, "checker-active", "ACTIVE", None, future, None),
        (5, 2, "checker-expired", "ACTIVE", None, past, None),
        (6, 2, "checker-revoked", "REVOKED", now.isoformat(), future, None),
    ])
    c.close()
    monkeypatch.setattr(md, "connect", connect)
    monkeypatch.setattr(md, "sync_operational_projection", lambda *args: None)

    key = "IAM-NEGATIVE-001"
    request = md.BulkRequest(domain=domain, rows=[{
        "record_key": key,
        "operation": "CREATE",
        "payload": {"name": "Synthetic IAM negative case"},
        "reason": "Disposable IAM UAT",
    }])
    for token in ("maker-expired", "maker-revoked"):
        with pytest.raises(HTTPException) as err:
            md.bulk_submit(request, x_m3_session=token)
        assert err.value.status_code == 401

    submitted = md.bulk_submit(request, x_m3_session="maker-active")
    assert submitted["pending"] == 1 and submitted["rejected"] == 0
    change_ref = submitted["rows"][0]["change_ref"]
    decision = md.Decision(decision="APPROVE")

    for token in ("checker-expired", "checker-revoked"):
        with pytest.raises(HTTPException) as err:
            md.decide(change_ref, decision, x_m3_session=token)
        assert err.value.status_code == 401

    c = connect()
    c.execute("UPDATE iam_user_roles SET status='INACTIVE' WHERE user_id=2")
    c.close()
    with pytest.raises(HTTPException) as err:
        md.decide(change_ref, decision, x_m3_session="checker-active")
    assert err.value.status_code == 403

    c = connect()
    assert c.execute(
        "SELECT status FROM md_change_requests WHERE change_ref=?", (change_ref,)
    ).fetchone()[0] == "PENDING"
    assert c.execute(
        "SELECT COUNT(*) FROM md_records WHERE domain=? AND record_key=?", (domain, key)
    ).fetchone()[0] == 0
    assert c.execute(
        "SELECT COUNT(*) FROM md_versions WHERE domain=? AND record_key=?", (domain, key)
    ).fetchone()[0] == 0
    assert c.execute(
        "SELECT COUNT(*) FROM md_audit WHERE domain=? AND record_key=? AND action='CHANGE_APPROVE_APPLY'",
        (domain, key)
    ).fetchone()[0] == 0
    c.execute("UPDATE iam_user_roles SET status='ACTIVE' WHERE user_id=2")
    c.close()

    approved = md.decide(change_ref, decision, x_m3_session="checker-active")
    assert approved["status"] == "APPROVED"
    c = connect()
    row = c.execute(
        "SELECT version,payload_json,approved_by FROM md_records WHERE domain=? AND record_key=?",
        (domain, key),
    ).fetchone()
    assert row["version"] == 1 and row["approved_by"] == "synthetic-checker"
    assert json.loads(row["payload_json"])["name"] == "Synthetic IAM negative case"
    assert c.execute(
        "SELECT COUNT(*) FROM md_versions WHERE domain=? AND record_key=?", (domain, key)
    ).fetchone()[0] == 1
    c.close()
