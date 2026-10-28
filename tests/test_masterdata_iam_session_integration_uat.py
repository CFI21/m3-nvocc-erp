"""Integrate actual IAM session and role enforcement with master-data changes.

The fixture uses a temporary SQLite database and disposable test-only sessions.
No seeded passwords, live credentials or production connections are used.
"""
import datetime
import secrets

import pytest
from fastapi import HTTPException

from app import admin, db, masterdata
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run


@pytest.fixture
def auth_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "iam_live_session_contract.db")
    monkeypatch.setattr(db, "DATABASE_URL", "")
    monkeypatch.delenv("M3_UAT_PRESERVE_DATA", raising=False)
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH


def disposable_session(username):
    c = db.connect()
    user = c.execute("SELECT id FROM iam_users WHERE username=?", (username,)).fetchone()
    assert user is not None
    token = secrets.token_urlsafe(32)
    now = datetime.datetime.now(datetime.timezone.utc)
    expiry = (now + datetime.timedelta(minutes=15)).isoformat()
    c.execute(
        "INSERT INTO iam_sessions(session_token,user_id,created_at,expires_at,"
        "last_seen_at,mfa_verified,status) VALUES(?,?,?,?,?,1,'ACTIVE')",
        (token, user["id"], now.isoformat(), expiry, now.isoformat()),
    )
    c.close()
    return token


def test_real_session_role_check_bulk_approval_and_revoke(auth_db):
    maker = disposable_session("md.maker")
    checker = disposable_session("md.checker")
    assert admin.whoami(x_m3_session=maker)["roles"] == ["MASTER_DATA"]
    assert admin.whoami(x_m3_session=checker)["roles"] == ["MASTER_DATA_MANAGER"]

    request = masterdata.BulkRequest(domain="customer", rows=[
        masterdata.BulkRow(
            record_key="UAT-IAM-SESSION-001", operation="CREATE",
            payload={"name": "Test Session Customer One"}, reason="Isolated IAM test",
        ),
        masterdata.BulkRow(
            record_key="UAT-IAM-SESSION-002", operation="CREATE",
            payload={"name": "Test Session Customer Two"}, reason="Isolated IAM test",
        ),
    ])
    preview = masterdata.bulk_preview(request, x_m3_session=maker)
    assert (preview["valid"], preview["rejected"]) == (2, 0)
    submitted = masterdata.bulk_submit(request, x_m3_session=maker)
    assert (submitted["pending"], submitted["rejected"]) == (2, 0)
    first_ref, second_ref = [row["change_ref"] for row in submitted["rows"]]

    with pytest.raises(HTTPException) as maker_denied:
        masterdata.decide(first_ref, masterdata.Decision(decision="APPROVE"),
                          x_m3_session=maker)
    assert maker_denied.value.status_code == 403
    assert maker_denied.value.detail["code"] == "CHECKER_PERMISSION_DENIED"

    approved = masterdata.decide(first_ref, masterdata.Decision(decision="APPROVE"),
                                 x_m3_session=checker)
    assert approved["status"] == "APPROVED"
    assert approved["version"] == 1
    rejected = masterdata.decide(second_ref, masterdata.Decision(decision="REJECT"),
                                 x_m3_session=checker)
    assert rejected["status"] == "REJECTED"

    history = masterdata.versions("customer", "UAT-IAM-SESSION-001",
                                  x_m3_session=checker)
    assert [(v["version"], v["change_ref"]) for v in history] == [(1, first_ref)]
    audit = masterdata.audit_list(change_ref=first_ref, x_m3_session=checker)
    assert {"CHANGE_REQUEST_CREATE", "CHANGE_APPROVE_APPLY"} <= {
        event["action"] for event in audit
    }

    assert admin.logout(x_m3_session=checker)["status"] == "LOGGED_OUT"
    with pytest.raises(HTTPException) as revoked:
        masterdata.versions("customer", "UAT-IAM-SESSION-001",
                            x_m3_session=checker)
    assert revoked.value.status_code == 401
    assert revoked.value.detail["code"] == "SESSION_INVALID"
    assert admin.logout(x_m3_session=maker)["status"] == "LOGGED_OUT"


def test_real_session_four_eyes_does_not_approve_own_request(auth_db):
    checker = disposable_session("md.checker")
    change = masterdata.create_change(
        masterdata.Change(
            domain="customer", record_key="UAT-IAM-SELF-001",
            operation="CREATE", payload={"name": "Own Request"},
            reason="Isolated four-eyes test",
        ),
        x_m3_session=checker,
    )
    ref = change["change_ref"]
    with pytest.raises(HTTPException) as denied:
        masterdata.decide(ref, masterdata.Decision(decision="APPROVE"),
                          x_m3_session=checker)
    assert denied.value.status_code == 409
    assert denied.value.detail["code"] == "FOUR_EYES_VIOLATION"
    db_conn = db.connect()
    assert db_conn.execute(
        "SELECT COUNT(*) FROM md_records WHERE record_key='UAT-IAM-SELF-001'"
    ).fetchone()[0] == 0
    db_conn.close()
    assert admin.logout(x_m3_session=checker)["status"] == "LOGGED_OUT"
