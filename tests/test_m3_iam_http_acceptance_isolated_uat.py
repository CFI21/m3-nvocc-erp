"""Isolated full HTTP contract for IAM, maker/checker, and master-data evidence.

All calls go through FastAPI TestClient with a disposable seeded SQLite database.
No browser claim, live credentials, Render, Neon, or production changes.
"""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.admin_seed import run as admin_seed_run
from app.main import app
from app.masterdata_seed import run as masterdata_seed_run
from app.seed import run as seed_run


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "isolated-http-iam.sqlite3")
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC", "OFF")
    monkeypatch.setenv("M3_UAT_TOKEN", "synthetic-http-acceptance-token")
    monkeypatch.delenv("M3_UAT_PRESERVE_DATA", raising=False)
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    with TestClient(
        app, raise_server_exceptions=False,
        headers={"X-M3-UAT-Token": "synthetic-http-acceptance-token"},
    ) as test_client:
        yield test_client


def session_headers(token):
    return {"X-M3-Session": token}


def login(client, username, password):
    response = client.post(
        "/api/admin/auth/login",
        json={"username": username, "password": password, "mfa_code": "123456"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["username"] == username
    assert body["mfa_verified"] is True
    assert body["session_token"]
    assert "SUPER_ADMIN" not in body["roles"]
    return body["session_token"]


def test_http_maker_checker_approval_and_audit_evidence(client):
    # Real HTTP middleware requires a separate UAT gate token.
    locked = client.get(
        "/api/masterdata/audit",
        headers={"X-M3-UAT-Token": "", "X-M3-Session": "forged"},
    )
    assert locked.status_code == 503
    assert locked.json()["detail"]["code"] == "PRODUCTION_TRAFFIC_LOCKED"

    incorrect_mfa = client.post(
        "/api/admin/auth/login",
        json={"username": "md.maker", "password": "Maker123!", "mfa_code": "000000"},
    )
    assert incorrect_mfa.status_code == 401
    assert incorrect_mfa.json()["detail"]["code"] == "MFA_REQUIRED"

    maker = login(client, "md.maker", "Maker123!")
    checker = login(client, "md.checker", "Checker123!")

    for username, token, required in (
        ("md.maker", maker, "MASTER_DATA"),
        ("md.checker", checker, "MASTER_DATA_MANAGER"),
    ):
        response = client.get("/api/admin/auth/whoami", headers=session_headers(token))
        assert response.status_code == 200, response.text
        assert response.json()["username"] == username
        assert required in response.json()["roles"]
        assert "SUPER_ADMIN" not in response.json()["roles"]

    # Spoofed role headers cannot unlock either history route.
    for url in ("/api/masterdata/audit", "/api/masterdata/versions/unit/UAT-HTTP-001"):
        for forged in ({}, {"X-M3-Session": "invalid", "X-Role": "SUPER_ADMIN"}):
            response = client.get(url, headers=forged)
            assert response.status_code == 401, (url, response.text)

    request = {
        "domain": "unit",
        "record_key": "UAT-HTTP-001",
        "operation": "CREATE",
        "payload": {"name": "Disposable HTTP maker/checker UAT"},
        "reason": "Isolated HTTP acceptance test",
    }
    created = client.post(
        "/api/masterdata/changes", headers=session_headers(maker), json=request
    )
    assert created.status_code == 200, created.text
    change_ref = created.json()["change_ref"]

    # No direct master mutation until a separate checker authorizes the request.
    before = client.get(
        "/api/masterdata/records/unit", headers=session_headers(maker)
    )
    assert before.status_code == 200, before.text
    assert not any(row["record_key"] == request["record_key"] for row in before.json())

    maker_approval = client.post(
        f"/api/masterdata/changes/{change_ref}/decision",
        headers=session_headers(maker),
        json={"decision": "APPROVE", "comment": "must be rejected"},
    )
    assert maker_approval.status_code in (403, 409), maker_approval.text

    decision = client.post(
        f"/api/masterdata/changes/{change_ref}/decision",
        headers=session_headers(checker),
        json={"decision": "APPROVE", "comment": "independent HTTP UAT"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "APPROVED"

    change_list = client.get(
        "/api/masterdata/changes", headers=session_headers(checker)
    )
    assert change_list.status_code == 200, change_list.text
    change = next(row for row in change_list.json() if row["change_ref"] == change_ref)
    assert change["status"] == "APPROVED"
    assert change["maker"] != change["checker"]

    records = client.get(
        "/api/masterdata/records/unit", headers=session_headers(checker)
    )
    assert records.status_code == 200, records.text
    record = next(row for row in records.json() if row["record_key"] == request["record_key"])
    assert record["approved_by"] == change["checker"]

    version = client.get(
        f"/api/masterdata/versions/unit/{request['record_key']}",
        headers=session_headers(checker),
    )
    assert version.status_code == 200, version.text
    assert any(row["change_ref"] == change_ref and row["version"] == record["version"]
               for row in version.json())

    # Scoped evidence must be complete and contain no unrelated audit events.
    audit = client.get(
        "/api/masterdata/audit",
        params={"change_ref": change_ref},
        headers=session_headers(checker),
    )
    assert audit.status_code == 200, audit.text
    related = audit.json()
    assert related
    assert all(row["change_ref"] == change_ref for row in related)
    assert client.get(
        "/api/masterdata/audit",
        params={"change_ref": "INVALID-REF"},
        headers=session_headers(checker),
    ).status_code == 422
    assert client.get(
        "/api/masterdata/audit",
        params={"change_ref": change_ref},
    ).status_code == 401
    assert "CHANGE_REQUEST_CREATE" in {row["action"] for row in related}
    assert any(row["action"] == "CHANGE_APPROVE_APPLY"
               and row["actor"] == change["checker"] for row in related)

    replay = client.post(
        f"/api/masterdata/changes/{change_ref}/decision",
        headers=session_headers(checker),
        json={"decision": "APPROVE"},
    )
    assert replay.status_code == 409, replay.text

    logout = client.post("/api/admin/auth/logout", headers=session_headers(checker))
    assert logout.status_code == 200, logout.text
    assert client.get(
        "/api/masterdata/audit", headers=session_headers(checker)
    ).status_code == 401


def test_http_audit_access_fails_after_role_or_user_deactivation(client):
    checker = login(client, "md.checker", "Checker123!")

    with db.connect() as conn:
        conn.execute(
            "UPDATE iam_roles SET status='INACTIVE' "
            "WHERE role_code='MASTER_DATA_MANAGER'"
        )
        conn.commit()
    denied = client.get("/api/masterdata/audit", headers=session_headers(checker))
    assert denied.status_code == 403, denied.text

    with db.connect() as conn:
        conn.execute(
            "UPDATE iam_roles SET status='ACTIVE' "
            "WHERE role_code='MASTER_DATA_MANAGER'"
        )
        conn.execute(
            "UPDATE iam_users SET status='INACTIVE' WHERE username='md.checker'"
        )
        conn.commit()
    revoked = client.get(
        "/api/masterdata/versions/unit/UAT-HTTP-001",
        headers=session_headers(checker),
    )
    assert revoked.status_code == 401, revoked.text
