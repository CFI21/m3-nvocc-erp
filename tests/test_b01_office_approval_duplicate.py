"""B01 Office checker approval must reject duplicate approved assignments.

Disposable SQLite only. No Neon access or IAM mutations.
"""
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app import masterdata


@pytest.fixture
def office_database(tmp_path, monkeypatch):
    filename = str(tmp_path / "office_checker_duplicate.sqlite3")

    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata,
        "authenticated_actor",
        lambda _db, token: (
            {"user_ref": "office-maker" if token == "maker" else "office-checker"},
            {"MASTER_DATA"} if token == "maker" else {"MASTER_DATA_MANAGER"},
        ),
    )
    db = connect()
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('agent','UAT-AGENT-001','{}','ACTIVE','now','now')"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS iam_countries "
        "(id INTEGER PRIMARY KEY,country_code TEXT,name TEXT,status TEXT)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS iam_offices "
        "(id INTEGER PRIMARY KEY,office_code TEXT,country_id INTEGER,status TEXT)"
    )
    db.execute("INSERT INTO iam_countries VALUES (1,'NL','Netherlands','ACTIVE')")
    db.execute("INSERT INTO iam_offices VALUES (1,'RTM',1,'ACTIVE')")
    db.close()
    return connect


def test_checker_rejects_competing_office_assignment(office_database):
    db = office_database()
    payload = {
        "config_type": "AGENT_OFFICE_ASSIGNMENT",
        "agent_code": "UAT-AGENT-001",
        "office_code": "RTM",
        "address": "Rotterdam",
    }
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('configuration','OFFICE-APPROVED',?,'ACTIVE','now','now')",
        (json.dumps(payload),),
    )
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES ('MDC-OFFICE-RACE','configuration','OFFICE-COMPETING','CREATE',?,'race','office-maker','now',0)",
        (json.dumps(payload),),
    )
    db.close()

    with pytest.raises(HTTPException) as error:
        masterdata.decide(
            "MDC-OFFICE-RACE",
            masterdata.Decision(decision="APPROVE", comment="review"),
            "checker",
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "DUPLICATE_AGENT_RELATIONSHIP"

    db = office_database()
    assert db.execute(
        "SELECT COUNT(*) FROM md_records "
        "WHERE domain='configuration' AND status='ACTIVE'"
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref='MDC-OFFICE-RACE'"
    ).fetchone()["status"] == "PENDING"
    db.close()
