"""Offline transaction tests against the *pinned* M3 atomic endpoint.

Uses a local temporary SQLite database and mocked authorization/row worker.
This exercises the real endpoint transaction, replay, and conflict control flow,
NOT a real PostgreSQL backend and NOT the production row-validation worker.
No network, credentials, or Neon writes.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

PINNED = "79968230bd5601142d9239c739853b64ed9d2f82"
PINNED_DIR = Path(__file__).resolve().parents[1] / "m3-pinned"
assert subprocess.check_output(["git", "-C", str(PINNED_DIR), "rev-parse", "HEAD"], text=True).strip() == PINNED
sys.path.insert(0, str(PINNED_DIR))
from app import masterdata as md  # noqa: E402
from app import db as db_module  # noqa: E402

KEY = "m3-disposable-offline-atomic-20261103"
ACTOR = {"user_ref": "UAT-B01-MAKER", "office_code": "RTM", "username": "md.maker"}


def request(second_name="Disposable B"):
    return md.BulkRequest(domain="customer", rows=[
        md.BulkRow(record_key="OFFLINE-A", operation="CREATE",
                   payload={"name": "Disposable A"}, reason="Isolated offline UAT only"),
        md.BulkRow(record_key="OFFLINE-B", operation="CREATE",
                   payload={"name": second_name}, reason="Isolated offline UAT only"),
    ])


@pytest.fixture
def harness(monkeypatch, tmp_path):
    dbfile = tmp_path / "atomic-fixture.sqlite3"
    conn = sqlite3.connect(str(dbfile), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE md_bulk_idempotency(
      maker TEXT, scope TEXT, idem_key TEXT, request_hash TEXT,
      response_json TEXT, created_at TEXT,
      UNIQUE(maker,scope,idem_key)
    );
    CREATE TABLE md_change_requests(
      id INTEGER PRIMARY KEY, record_key TEXT, change_ref TEXT
    );
    CREATE TABLE md_audit(
      id INTEGER PRIMARY KEY, record_key TEXT, change_ref TEXT
    );
    """)
    monkeypatch.setattr(md, "connect", lambda: conn)
    monkeypatch.setattr(md, "using_postgres", lambda: False)
    monkeypatch.setattr(db_module, "using_postgres", lambda: False)
    monkeypatch.setattr(md, "authenticated_actor", lambda *_: (ACTOR, {"MASTER_DATA"}))
    state = {"calls": 0, "fail_on": None}

    def worker(b, token, c=None, owns_transaction=True):
        assert c is conn and owns_transaction is False
        assert token == "offline-session-placeholder"
        state["calls"] += 1
        if state["calls"] == state["fail_on"]:
            raise HTTPException(422, {"code": "OFFLINE_SECOND_ROW_REJECTED"})
        ref = f"OFFLINE-{state['calls']}"
        c.execute("INSERT INTO md_change_requests(record_key,change_ref) VALUES(?,?)",
                  (b.record_key, ref))
        c.execute("INSERT INTO md_audit(record_key,change_ref) VALUES(?,?)",
                  (b.record_key, ref))
        return {"change_ref": ref}

    monkeypatch.setattr(md, "_create_change_worker", worker)
    yield conn, state
    conn.close()


def counts(conn):
    return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                 for table in ("md_change_requests", "md_audit", "md_bulk_idempotency"))


def submit(payload, key=KEY):
    return md.bulk_submit_atomic(
        payload, idempotency_key=key, x_m3_session="offline-session-placeholder")


def test_second_row_rejection_undoes_first_row_and_audit_and_ledger(harness):
    conn, state = harness
    state["fail_on"] = 2
    assert counts(conn) == (0, 0, 0)
    with pytest.raises(HTTPException) as err:
        submit(request())
    assert err.value.status_code == 422
    assert counts(conn) == (0, 0, 0)
    # Retry with identical key after rollback must be eligible, not a stale replay.
    state["fail_on"] = None
    result = submit(request())
    assert result["pending"] == 2 and result["atomic"] is True
    assert counts(conn) == (2, 2, 1)


def test_replay_is_idempotent_and_does_not_run_worker_twice(harness):
    conn, state = harness
    first = submit(request())
    assert counts(conn) == (2, 2, 1)
    assert state["calls"] == 2
    second = submit(request())
    assert second == first
    assert state["calls"] == 2
    assert counts(conn) == (2, 2, 1)


def test_same_key_different_payload_returns_conflict_without_changes(harness):
    conn, state = harness
    submit(request())
    snapshot = counts(conn)
    with pytest.raises(HTTPException) as err:
        submit(request("Different name"))
    assert err.value.status_code == 409
    assert err.value.detail["code"] == "BULK_IDEMPOTENCY_PAYLOAD_CONFLICT"
    assert counts(conn) == snapshot
    assert state["calls"] == 2


def test_checker_without_maker_role_denied_and_no_ledger(harness, monkeypatch):
    conn, state = harness
    monkeypatch.setattr(md, "authenticated_actor",
                        lambda *_: ({"user_ref":"UAT-CHECKER","office_code":"RTM"}, {"VIEWER"}))
    with pytest.raises(HTTPException) as err:
        submit(request())
    assert err.value.status_code == 403
    assert counts(conn) == (0, 0, 0)
    assert state["calls"] == 0


@pytest.mark.parametrize("bad", ["", "short", "x" * 129])
def test_bad_idempotency_key_fails_before_opening_transaction(harness, bad):
    conn, state = harness
    with pytest.raises(HTTPException) as err:
        submit(request(), key=bad)
    assert err.value.status_code == 422
    assert counts(conn) == (0, 0, 0)
    assert state["calls"] == 0
