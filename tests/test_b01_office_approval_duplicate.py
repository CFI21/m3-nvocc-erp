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


def test_office_update_cannot_take_existing_agent_office(office_database):
    """Checker must reject an UPDATE converging onto another approved Office key."""
    db = office_database()
    old = {
        "config_type": "AGENT_OFFICE_ASSIGNMENT",
        "agent_code": "UAT-AGENT-001",
        "office_code": "AMS",
        "address": "Amsterdam",
    }
    approved = dict(old, office_code="RTM", address="Rotterdam")
    for key, payload in (("OFFICE-EXISTING", approved), ("OFFICE-UPDATE", old)):
        db.execute(
            "INSERT INTO md_records"
            "(domain,record_key,payload_json,status,version,created_at,updated_at) "
            "VALUES ('configuration',?,?,'ACTIVE',1,'now','now')",
            (key, json.dumps(payload)),
        )
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES ('MDC-OFFICE-UPDATE','configuration','OFFICE-UPDATE','UPDATE',?,'review','office-maker','now',1)",
        (json.dumps({"office_code": "rtm"}),),
    )
    db.close()

    with pytest.raises(HTTPException) as error:
        masterdata.decide(
            "MDC-OFFICE-UPDATE",
            masterdata.Decision(decision="APPROVE", comment="independent review"),
            "checker",
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "DUPLICATE_AGENT_RELATIONSHIP"

    db = office_database()
    unchanged = db.execute(
        "SELECT payload_json,version FROM md_records "
        "WHERE domain='configuration' AND record_key='OFFICE-UPDATE'"
    ).fetchone()
    assert json.loads(unchanged["payload_json"]) == old
    assert unchanged["version"] == 1
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref='MDC-OFFICE-UPDATE'"
    ).fetchone()["status"] == "PENDING"
    db.close()


def test_checker_approval_preserves_iam_roles_and_country_derivation(office_database):
    """Duplicate-approval rejection cannot modify IAM or resolved countries."""
    db = office_database()
    db.execute(
        "CREATE TABLE iam_user_roles "
        "(id INTEGER PRIMARY KEY,user_id INTEGER,office_id INTEGER)"
    )
    db.execute("INSERT INTO iam_user_roles VALUES (1,7,1)")
    payload = {
        "config_type": "AGENT_OFFICE_ASSIGNMENT",
        "agent_code": "UAT-AGENT-001",
        "office_code": "RTM",
        "address": "Rotterdam",
    }
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('configuration','OFFICE-PRIMARY',?,'ACTIVE','now','now')",
        (json.dumps(payload),),
    )
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES ('MDC-OFFICE-IAM','configuration','OFFICE-DUP','CREATE',?,'review','office-maker','now',0)",
        (json.dumps(payload),),
    )
    before = [tuple(row) for row in db.execute("SELECT * FROM iam_user_roles")]
    db.close()

    assert masterdata.agent_office_countries("maker") == [{
        "agent_code": "UAT-AGENT-001",
        "office_code": "RTM",
        "source": "OFFICE",
        "country_code": "NL",
        "country_name": "Netherlands",
    }]
    with pytest.raises(HTTPException) as err:
        masterdata.decide(
            "MDC-OFFICE-IAM",
            masterdata.Decision(decision="APPROVE", comment="reject collision"),
            "checker",
        )
    assert err.value.status_code == 409
    db = office_database()
    assert [tuple(row) for row in db.execute("SELECT * FROM iam_user_roles")] == before
    assert db.execute(
        "SELECT COUNT(*) FROM md_records WHERE domain='configuration' AND status='ACTIVE'"
    ).fetchone()[0] == 1
    db.close()


def test_maker_cannot_submit_colliding_office_update(office_database):
    """Fail before queuing a duplicate Office assignment."""
    db=office_database()
    base={"config_type":"AGENT_OFFICE_ASSIGNMENT","agent_code":"UAT-AGENT-001"}
    for key, office in (("OFFICE-RTM","RTM"),("OFFICE-AMS","AMS")):
        db.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,version,created_at,updated_at) "
            "VALUES ('configuration',?,?,'ACTIVE',1,'now','now')",
            (key,json.dumps({**base,"office_code":office})),
        )
    db.close()

    with pytest.raises(HTTPException) as error:
        masterdata.create_change(
            masterdata.Change(
                domain="configuration",record_key="OFFICE-AMS",
                operation="UPDATE",payload={"office_code":"rtm"},
                reason="Duplicate routing attempt",
            ),
            "maker",
        )
    assert error.value.status_code==409
    assert error.value.detail["code"]=="DUPLICATE_AGENT_RELATIONSHIP"
    db=office_database()
    assert db.execute(
        "SELECT COUNT(*) FROM md_change_requests WHERE record_key='OFFICE-AMS'"
    ).fetchone()[0]==0
    db.close()
