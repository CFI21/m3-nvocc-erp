"""M3 isolated HTTP-level atomic bulk acceptance with *real* seeded IAM.

Uses a temporary SQLite database and FastAPI TestClient. No Render, Neon,
ANCLINE, production credentials, or financial integrations are accessed.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.admin_seed import run as seed_iam
from app.masterdata_seed import run as seed_masterdata
from app.main import app
from app.seed import run as seed_system


@pytest.fixture
def sandbox_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "m3-http-atomic.sqlite3")
    monkeypatch.setattr(db, "DATABASE_URL", "")
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC", "OFF")
    monkeypatch.setenv("M3_LIVE_PROVIDERS", "OFF")
    monkeypatch.setenv("M3_REAL_MONEY", "OFF")
    monkeypatch.setenv("M3_UAT_TOKEN", "fixture-m3-atomic-http-only")
    monkeypatch.delenv("M3_UAT_PRESERVE_DATA", raising=False)
    seed_system(True)
    seed_iam()
    seed_masterdata()
    conn = db.connect()
    try:
        conn.execute("""CREATE TABLE md_bulk_idempotency(
            maker TEXT NOT NULL, scope TEXT NOT NULL, idem_key TEXT NOT NULL,
            request_hash TEXT NOT NULL, response_json TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(maker,scope,idem_key))""")
    finally:
        conn.close()

    with TestClient(app, raise_server_exceptions=False,
                    headers={"X-M3-UAT-Token": "fixture-m3-atomic-http-only"}) as client:
        yield client


def auth(client, username, password):
    response = client.post("/api/admin/auth/login",
                           json={"username": username, "password": password, "mfa_code": "123456"})
    assert response.status_code == 200, response.text
    assert response.json()["mfa_verified"] is True
    assert "SUPER_ADMIN" not in response.json()["roles"]
    return {"X-M3-Session": response.json()["session_token"]}


def payload(*keys):
    return {
        "domain": "agent",
        "rows": [{
            "record_key": key, "operation": "CREATE",
            "payload": {"name": "Isolated fixture " + key},
            "reason": "Disposable HTTP atomic validation",
        } for key in keys],
    }


def test_actual_http_iam_atomic_bulk_replay_recovery_and_checker_reject(sandbox_client):
    client = sandbox_client
    invalid_mfa = client.post("/api/admin/auth/login", json={
        "username": "md.maker", "password": "Maker123!", "mfa_code": "000000"})
    assert invalid_mfa.status_code == 401
    assert invalid_mfa.json()["detail"]["code"] == "MFA_REQUIRED"
    maker = auth(client, "md.maker", "Maker123!")
    checker = auth(client, "md.checker", "Checker123!")

    locked = client.post("/api/masterdata/bulk/submit-atomic",
                         headers={**maker, "X-M3-UAT-Token": "",
                                  "Idempotency-Key": "fixture-http-atomic-1"},
                         json=payload("M3-HTTP-A", "M3-HTTP-B"))
    assert locked.status_code == 503
    assert locked.json()["detail"]["code"] == "PRODUCTION_TRAFFIC_LOCKED"

    data = payload("M3-HTTP-A", "M3-HTTP-B")
    headers = {**maker, "Idempotency-Key": "fixture-http-atomic-1"}
    first = client.post("/api/masterdata/bulk/submit-atomic", headers=headers, json=data)
    assert first.status_code == 200, first.text
    first_result = first.json()
    assert first_result["atomic"] is True
    assert first_result["pending"] == 2
    refs = [row["change_ref"] for row in first_result["rows"]]
    assert len(set(refs)) == 2

    replay = client.post("/api/masterdata/bulk/submit-atomic", headers=headers, json=data)
    assert replay.status_code == 200 and replay.json() == first_result
    recovery = client.get("/api/masterdata/bulk/submit-atomic/recovery",
                          params={"idempotency_key": "fixture-http-atomic-1"}, headers=maker)
    assert recovery.status_code == 200 and recovery.json() == first_result

    altered = payload("M3-HTTP-A", "M3-HTTP-C")
    conflict = client.post("/api/masterdata/bulk/submit-atomic",
                           headers=headers, json=altered)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "BULK_IDEMPOTENCY_PAYLOAD_CONFLICT"

    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM md_change_requests WHERE status='PENDING'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM md_bulk_idempotency").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM md_records WHERE record_key LIKE 'M3-HTTP-%'").fetchone()[0] == 0
    conn.close()

    for ref in refs:
        path = f"/api/masterdata/changes/{ref}/decision"
        forbidden = client.post(path, headers=maker, json={
            "decision": "REJECT", "comment": "deny maker self decision"})
        assert forbidden.status_code == 403
        accepted = client.post(path, headers=checker, json={
            "decision": "REJECT", "comment": "isolated UAT rejection"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "REJECTED"

    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM md_change_requests WHERE status='PENDING'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM md_records WHERE record_key LIKE 'M3-HTTP-%'").fetchone()[0] == 0
    conn.close()
    for session in (maker, checker):
        assert client.post("/api/admin/auth/logout", headers=session).status_code == 200


def test_actual_http_atomic_rollback_on_second_row_collision(sandbox_client):
    client = sandbox_client
    maker = auth(client, "md.maker", "Maker123!")
    checker = auth(client, "md.checker", "Checker123!")
    previous = client.post("/api/masterdata/changes", headers=maker, json={
        "domain": "agent", "record_key": "M3-HTTP-PREEXIST",
        "operation": "CREATE", "payload": {"name": "Existing pending record"},
        "reason": "Fixture preexisting change",
    })
    assert previous.status_code == 200, previous.text
    previous_ref = previous.json()["change_ref"]

    failure = client.post("/api/masterdata/bulk/submit-atomic",
                          headers={**maker, "Idempotency-Key": "fixture-http-rollback-1"},
                          json=payload("M3-HTTP-ROLLBACK-FIRST", "M3-HTTP-PREEXIST"))
    assert failure.status_code == 409, failure.text

    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM md_change_requests WHERE record_key='M3-HTTP-ROLLBACK-FIRST'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM md_change_requests WHERE record_key='M3-HTTP-PREEXIST'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM md_bulk_idempotency").fetchone()[0] == 0
    conn.close()

    clean = client.post(f"/api/masterdata/changes/{previous_ref}/decision",
                        headers=checker, json={"decision": "REJECT", "comment": "fixture cleanup"})
    assert clean.status_code == 200 and clean.json()["status"] == "REJECTED"
    for sess in (maker, checker):
        assert client.post("/api/admin/auth/logout", headers=sess).status_code == 200
