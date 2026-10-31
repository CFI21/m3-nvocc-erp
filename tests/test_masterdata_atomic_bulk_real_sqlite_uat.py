"""Real SQLite transaction acceptance for M3 atomic bulk (offline only).

No Render, Neon, ANCLINE, live credentials or production database access.
"""
import sqlite3

import pytest
from fastapi import HTTPException

from app import masterdata as md


@pytest.fixture
def isolated_bulk(tmp_path, monkeypatch):
    db = tmp_path / "isolated-atomic-bulk.sqlite3"

    def connect():
        conn = sqlite3.connect(db, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    conn = connect()
    md.init_schema(conn)
    conn.execute(
        """CREATE TABLE md_bulk_idempotency (
            maker TEXT NOT NULL, scope TEXT NOT NULL, idem_key TEXT NOT NULL,
            request_hash TEXT NOT NULL, response_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(maker, scope, idem_key)
        )"""
    )
    conn.close()

    identities = {
        "maker": ({"user_ref": "uat-maker", "username": "md.maker", "office_code": "RTM"}, {"MASTER_DATA"}),
        "checker": ({"user_ref": "uat-checker", "username": "md.checker", "office_code": "RTM"}, {"MASTER_DATA_MANAGER"}),
    }

    def auth(_conn, token):
        if token not in identities:
            raise HTTPException(401, {"code": "SESSION_INVALID"})
        return identities[token]

    monkeypatch.setattr(md, "connect", connect)
    monkeypatch.setattr(md, "using_postgres", lambda: False)
    monkeypatch.setattr(md, "authenticated_actor", auth)
    monkeypatch.setattr(md, "sync_operational_projection", lambda *args, **kwargs: None)
    return connect


def request(*keys):
    return md.BulkRequest(
        domain="agent",
        rows=[md.BulkRow(record_key=k, operation="CREATE",
                         payload={"name": "Disposable " + k},
                         reason="offline atomic test") for k in keys],
    )


def test_atomic_commit_replay_conflict_recovery_and_checker_reject(isolated_bulk):
    payload = request("UAT-ATOMIC-A", "UAT-ATOMIC-B")
    result = md.bulk_submit_atomic(payload, "offline-atomic-001", "maker")
    assert result["atomic"] is True and result["pending"] == 2
    conn = isolated_bulk()
    assert conn.execute("SELECT COUNT(*) FROM md_change_requests").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM md_bulk_idempotency").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM md_records").fetchone()[0] == 0
    conn.close()
    assert md.bulk_submit_atomic(payload, "offline-atomic-001", "maker") == result
    assert md.bulk_atomic_recovery("offline-atomic-001", "maker") == result
    with pytest.raises(HTTPException) as exc:
        md.bulk_submit_atomic(request("UAT-ATOMIC-A", "UAT-DIFFERENT"), "offline-atomic-001", "maker")
    assert exc.value.status_code == 409

    for row in result["rows"]:
        ref = row["change_ref"]
        with pytest.raises(HTTPException) as exc:
            md.decide(ref, md.Decision(decision="REJECT"), x_m3_session="maker")
        assert exc.value.status_code in (403, 409)
        rejected = md.decide(ref, md.Decision(decision="REJECT", comment="offline UAT cleanup"), x_m3_session="checker")
        assert rejected["status"] == "REJECTED"

    conn = isolated_bulk()
    assert conn.execute("SELECT COUNT(*) FROM md_change_requests WHERE status='PENDING'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM md_records").fetchone()[0] == 0
    conn.close()


def test_atomic_late_failure_rolls_back_first_row_and_ledger(isolated_bulk):
    # Existing pending key makes row 2 fail *after* row 1 was inserted.
    prior = md.bulk_submit(request("UAT-PREEXISTING"), x_m3_session="maker")
    assert prior["pending"] == 1
    with pytest.raises(HTTPException) as exc:
        md.bulk_submit_atomic(request("UAT-ROLLBACK-A", "UAT-PREEXISTING"),
                              "offline-rollback-001", "maker")
    assert exc.value.status_code == 409
    conn = isolated_bulk()
    assert conn.execute("SELECT COUNT(*) FROM md_change_requests WHERE record_key='UAT-ROLLBACK-A'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM md_change_requests WHERE record_key='UAT-PREEXISTING'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM md_bulk_idempotency").fetchone()[0] == 0
    conn.close()
