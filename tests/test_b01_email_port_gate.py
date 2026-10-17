"""B01 Email reference gate: independent read-only disposable DB checks.

No Neon data, IAM changes, port seeding in deployed environments, or production calls.
"""
import sqlite3

import pytest

from app import masterdata


def _db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "b01_email_reference.sqlite3"))
    conn.row_factory = sqlite3.Row
    masterdata.init_schema(conn)
    return conn


def _email(port="NLRTM"):
    return {
        "config_type": "AGENT_EMAIL",
        "agent_code": "UAT-AGENT-001",
        "port_code": port,
        "email": "ops@example.org",
    }


def test_missing_port_fails_closed(tmp_path):
    conn = _db(tmp_path)
    try:
        errors = masterdata.reference_errors(conn, "configuration", _email())
        assert "AGENT_EMAIL_PORT_REFERENCE_NOT_ACTIVE" in errors
    finally:
        conn.close()


def test_inactive_port_fails_closed(tmp_path):
    conn = _db(tmp_path)
    try:
        conn.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
            "VALUES ('port','NLRTM','{}','INACTIVE','now','now')"
        )
        errors = masterdata.reference_errors(conn, "configuration", _email())
        assert "AGENT_EMAIL_PORT_REFERENCE_NOT_ACTIVE" in errors
    finally:
        conn.close()


def test_active_authoritative_port_resolves_without_bypass(tmp_path):
    conn = _db(tmp_path)
    try:
        conn.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
            "VALUES ('port','NLRTM','{}','ACTIVE','now','now')"
        )
        errors = masterdata.reference_errors(conn, "configuration", _email())
        assert "AGENT_EMAIL_PORT_REFERENCE_NOT_ACTIVE" not in errors
        # The agent itself is still required to be active; passing the port gate is not approval.
        assert "AGENT_REFERENCE_NOT_ACTIVE" in errors
    finally:
        conn.close()


def test_email_requires_independent_checker_and_keeps_audit(tmp_path, monkeypatch):
    """End-to-end governance against disposable SQLite only."""
    import json
    import pytest
    from fastapi import HTTPException

    dbfile = str(tmp_path / "b01_email_lifecycle.sqlite3")

    def connection():
        c = sqlite3.connect(dbfile, isolation_level=None)
        c.row_factory = sqlite3.Row
        masterdata.init_schema(c)
        return c

    monkeypatch.setattr(masterdata, "connect", connection)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)

    def actor(_connection, token):
        if token == "maker":
            return {"user_ref": "email-maker", "username": "uat.actor"}, {"MASTER_DATA"}
        if token == "checker":
            return {"user_ref": "email-checker", "username": "uat.actor"}, {"MASTER_DATA_MANAGER"}
        raise HTTPException(401, "Unknown actor")

    monkeypatch.setattr(masterdata, "authenticated_actor", actor)
    c = connection()
    for domain, key in (("agent", "UAT-AGENT-001"), ("port", "NLRTM")):
        c.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
            "VALUES (?,?,?,'ACTIVE','now','now')",
            (domain, key, json.dumps({"unlocode": key} if domain == "port" else {"name": "UAT Agent"})),
        )
    c.close()

    submitted = masterdata.create_change(
        masterdata.Change(
            domain="configuration", record_key="EMAIL-GOV-001",
            operation="CREATE", payload=_email(), reason="B01 governed email registration"
        ), "maker"
    )
    assert submitted["status"] == "PENDING"
    c = connection()
    assert c.execute(
        "SELECT COUNT(*) FROM md_records WHERE domain='configuration' AND record_key='EMAIL-GOV-001'"
    ).fetchone()[0] == 0
    c.close()

    with pytest.raises(HTTPException) as denied:
        masterdata.decide(
            submitted["change_ref"],
            masterdata.Decision(decision="APPROVE", comment="self approval"),
            "maker",
        )
    assert denied.value.status_code in (403, 409)

    approved = masterdata.decide(
        submitted["change_ref"],
        masterdata.Decision(decision="APPROVE", comment="independent verification"),
        "checker",
    )
    assert approved["status"] == "APPROVED"
    c = connection()
    record = c.execute(
        "SELECT status,payload_json,version FROM md_records WHERE domain='configuration' AND record_key='EMAIL-GOV-001'"
    ).fetchone()
    assert record["status"] == "ACTIVE"
    assert json.loads(record["payload_json"]) == _email()
    assert record["version"] == 1
    assert c.execute(
        "SELECT COUNT(*) FROM md_versions WHERE domain='configuration' AND record_key='EMAIL-GOV-001'"
    ).fetchone()[0] == 1
    c.close()


def test_checker_rejects_email_and_preserves_no_active_record(tmp_path, monkeypatch):
    """Rejected requests remain historical only; no active configuration is created."""
    import json
    from app import masterdata

    dbfile = str(tmp_path / "b01_email_rejection.sqlite3")

    def connection():
        c = sqlite3.connect(dbfile, isolation_level=None)
        c.row_factory = sqlite3.Row
        masterdata.init_schema(c)
        return c

    monkeypatch.setattr(masterdata, "connect", connection)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _c, token: (
            {"user_ref": "email-maker" if token == "maker" else "email-checker", "username": "uat.actor"},
            {"MASTER_DATA"} if token == "maker" else {"MASTER_DATA_MANAGER"},
        ),
    )
    c = connection()
    for domain, key in (("agent", "UAT-AGENT-001"), ("port", "NLRTM")):
        c.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
            "VALUES (?,?,?,'ACTIVE','now','now')",
            (domain,key,json.dumps({"unlocode":key} if domain=="port" else {"name":"UAT Agent"})),
        )
    c.close()
    pending = masterdata.create_change(
        masterdata.Change(
            domain="configuration",record_key="EMAIL-REJECT-001",
            operation="CREATE",payload=_email(),reason="B01 review",
        ),"maker",
    )
    rejected = masterdata.decide(
        pending["change_ref"],
        masterdata.Decision(decision="REJECT",comment="Not authorized"),
        "checker",
    )
    assert rejected["status"] == "REJECTED"
    c = connection()
    assert c.execute(
        "SELECT COUNT(*) FROM md_records WHERE domain='configuration' AND record_key='EMAIL-REJECT-001'"
    ).fetchone()[0] == 0
    assert c.execute(
        "SELECT status FROM md_change_requests WHERE change_ref=?",
        (pending["change_ref"],),
    ).fetchone()["status"] == "REJECTED"
    c.close()


def test_checker_approval_rechecks_email_uniqueness(tmp_path, monkeypatch):
    """Concurrent pending requests cannot activate duplicate email routes."""
    import json
    import pytest
    from fastapi import HTTPException

    dbfile = str(tmp_path / "b01_email_approval_race.sqlite3")

    def connection():
        c = sqlite3.connect(dbfile, isolation_level=None)
        c.row_factory = sqlite3.Row
        masterdata.init_schema(c)
        return c

    monkeypatch.setattr(masterdata, "connect", connection)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _c, token: (
            {"user_ref": "maker" if token == "maker" else "checker", "username": "uat.actor"},
            {"MASTER_DATA"} if token == "maker" else {"MASTER_DATA_MANAGER"},
        ),
    )
    c = connection()
    for domain, key in (("agent", "UAT-AGENT-001"), ("port", "NLRTM")):
        c.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
            "VALUES (?,?,?,'ACTIVE','now','now')",
            (domain, key, json.dumps({"unlocode": key} if domain == "port" else {"name": "UAT Agent"})),
        )
    c.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('configuration','EMAIL-ACTIVE',?,'ACTIVE','now','now')",
        (json.dumps(_email()),),
    )
    c.execute(
        "INSERT INTO md_change_requests(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES ('MDC-TEST-DUP','configuration','EMAIL-DUP','CREATE',?,'concurrent','maker','now',0)",
        (json.dumps(_email()),),
    )
    c.close()
    with pytest.raises(HTTPException) as exc:
        masterdata.decide(
            "MDC-TEST-DUP",
            masterdata.Decision(decision="APPROVE", comment="check"),
            "checker",
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "DUPLICATE_AGENT_RELATIONSHIP"
    c = connection()
    assert c.execute(
        "SELECT status FROM md_change_requests WHERE change_ref='MDC-TEST-DUP'"
    ).fetchone()["status"] == "PENDING"
    assert c.execute(
        "SELECT COUNT(*) FROM md_records WHERE domain='configuration' AND status='ACTIVE'"
    ).fetchone()[0] == 1
    c.close()


def test_checker_revalidates_port_after_submission(tmp_path, monkeypatch):
    """A Port retired after submission must block final Email approval."""
    import json
    import pytest
    from fastapi import HTTPException

    filename = str(tmp_path / "email_port_revocation.sqlite3")

    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, token: (
            {"user_ref": "email-maker" if token == "maker" else "email-checker", "username": "uat.actor"},
            {"MASTER_DATA"} if token == "maker" else {"MASTER_DATA_MANAGER"},
        ),
    )
    db = connect()
    for domain, key in (("agent", "UAT-AGENT-001"), ("port", "NLRTM")):
        db.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
            "VALUES (?,?,?,'ACTIVE','now','now')",
            (domain, key, json.dumps({"unlocode": key} if domain == "port" else {"name": "UAT Agent"})),
        )
    db.close()

    request = masterdata.create_change(
        masterdata.Change(
            domain="configuration",
            record_key="EMAIL-REVOKED-PORT",
            operation="CREATE",
            payload=_email(),
            reason="Reference lifecycle test",
        ),
        "maker",
    )
    assert request["status"] == "PENDING"

    db = connect()
    db.execute("UPDATE md_records SET status='INACTIVE' WHERE domain='port' AND record_key='NLRTM'")
    db.close()
    with pytest.raises(HTTPException) as error:
        masterdata.decide(
            request["change_ref"],
            masterdata.Decision(decision="APPROVE", comment="independent review"),
            "checker",
        )
    assert error.value.status_code == 422
    assert "AGENT_EMAIL_PORT_REFERENCE_NOT_ACTIVE" in error.value.detail["codes"]

    db = connect()
    assert db.execute(
        "SELECT COUNT(*) FROM md_records "
        "WHERE domain='configuration' AND record_key='EMAIL-REVOKED-PORT'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref=?",
        (request["change_ref"],),
    ).fetchone()["status"] == "PENDING"
    db.close()


def test_maker_rejects_duplicate_agent_email_update_before_queue(tmp_path, monkeypatch):
    """Normalized case and whitespace must not bypass the Email uniqueness gate."""
    import json
    import pytest
    from fastapi import HTTPException

    filename = str(tmp_path / "email_update_duplicate.sqlite3")

    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: ({"user_ref": "email-maker", "username": "uat.actor"}, {"MASTER_DATA"}),
    )

    db = connect()
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('agent','UAT-AGENT-001','{}','ACTIVE','now','now')"
    )
    for port in ("NLRTM", "NLAMS"):
        db.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
            "VALUES ('port',?,'{}','ACTIVE','now','now')", (port,)
        )
    for key, port, email in (
        ("EMAIL-EXISTING", "NLRTM", "ops@example.org"),
        ("EMAIL-CHANGING", "NLAMS", "other@example.org"),
    ):
        db.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,version,created_at,updated_at) "
            "VALUES ('configuration',?,?,'ACTIVE',1,'now','now')",
            (key,json.dumps({**_email(port), "email":email})),
        )
    db.close()

    with pytest.raises(HTTPException) as err:
        masterdata.create_change(
            masterdata.Change(
                domain="configuration",
                record_key="EMAIL-CHANGING",
                operation="UPDATE",
                payload={"port_code":"nlrtm", "email":" OPS@EXAMPLE.ORG "},
                reason="Duplicate email update",
            ),
            "maker",
        )
    assert err.value.status_code == 409
    assert err.value.detail["code"] == "DUPLICATE_AGENT_RELATIONSHIP"

    db = connect()
    assert db.execute(
        "SELECT COUNT(*) FROM md_change_requests WHERE record_key='EMAIL-CHANGING'"
    ).fetchone()[0] == 0
    db.close()


def test_checker_create_fails_cleanly_when_key_was_claimed(tmp_path, monkeypatch):
    """Concurrent approval cannot overwrite a record claimed after submission."""
    import json
    import pytest
    from fastapi import HTTPException

    filename = str(tmp_path / "claimed_key.sqlite3")

    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(masterdata, "authenticated_actor", lambda _db, _token: (
        {"user_ref": "checker", "username": "uat.actor"}, {"MASTER_DATA_MANAGER"}
    ))
    db = connect()
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES ('MDC-CLAIMED','configuration','EMAIL-CLAIMED','CREATE',?,'race','maker','now',0)",
        (json.dumps(_email()),),
    )
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,version,created_at,updated_at) "
        "VALUES ('configuration','EMAIL-CLAIMED','{}','ACTIVE',1,'now','now')"
    )
    db.close()
    with pytest.raises(HTTPException) as err:
        masterdata.decide(
            "MDC-CLAIMED",
            masterdata.Decision(decision="APPROVE", comment="review"),
            "checker",
        )
    assert err.value.status_code == 409
    assert err.value.detail["code"] == "DUPLICATE_MASTER_KEY"
    db = connect()
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref='MDC-CLAIMED'"
    ).fetchone()["status"] == "PENDING"
    assert db.execute(
        "SELECT payload_json FROM md_records WHERE record_key='EMAIL-CLAIMED'"
    ).fetchone()["payload_json"] == "{}"
    db.close()


@pytest.mark.parametrize("operation", ["UPDATE", "DEACTIVATE", "REACTIVATE"])
def test_checker_rejects_missing_master_for_noncreate(tmp_path, monkeypatch, operation):
    """A stale pending change must not crash or recreate a missing record."""
    import json
    from fastapi import HTTPException
    import pytest

    filename = str(tmp_path / ("missing_" + operation + ".sqlite3"))
    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(masterdata, "authenticated_actor", lambda _db, _token: (
        {"user_ref": "checker", "username": "uat.actor"}, {"MASTER_DATA_MANAGER"}
    ))
    db = connect()
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES (?,?,?,?,?,'stale','maker','now',1)",
        ("MDC-MISSING-" + operation, "configuration", "MISSING-EMAIL", operation, json.dumps({})),
    )
    db.close()
    with pytest.raises(HTTPException) as error:
        masterdata.decide(
            "MDC-MISSING-" + operation,
            masterdata.Decision(decision="APPROVE", comment="review"),
            "checker",
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "MASTER_RECORD_NOT_FOUND"
    db = connect()
    assert db.execute("SELECT COUNT(*) FROM md_records WHERE record_key='MISSING-EMAIL'").fetchone()[0] == 0
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref=?",
        ("MDC-MISSING-" + operation,),
    ).fetchone()["status"] == "PENDING"
    db.close()


def test_same_email_port_allowed_for_different_agents(tmp_path, monkeypatch):
    """Semantic duplicate protection is Agent-scoped, not globally email-scoped."""
    import json

    filename = str(tmp_path / "email_cross_agent.sqlite3")

    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: ({"user_ref": "email-maker", "username": "uat.actor"}, {"MASTER_DATA"}),
    )
    db = connect()
    for agent in ("UAT-AGENT-001", "UAT-AGENT-002"):
        db.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
            "VALUES ('agent',?,?,'ACTIVE','now','now')",
            (agent, json.dumps({"name": agent})),
        )
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('port','NLRTM','{}','ACTIVE','now','now')"
    )
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('configuration','EMAIL-AGENT-001',?,'ACTIVE','now','now')",
        (json.dumps(_email()),),
    )
    db.close()
    payload = {**_email(), "agent_code": "UAT-AGENT-002"}
    pending = masterdata.create_change(
        masterdata.Change(
            domain="configuration",
            record_key="EMAIL-AGENT-002",
            operation="CREATE",
            payload=payload,
            reason="Distinct agent route",
        ),
        "maker",
    )
    assert pending["status"] == "PENDING"
    db = connect()
    assert db.execute(
        "SELECT COUNT(*) FROM md_records WHERE domain='configuration' AND status='ACTIVE'"
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM md_change_requests WHERE record_key='EMAIL-AGENT-002' AND status='PENDING'"
    ).fetchone()[0] == 1
    db.close()


def test_same_agent_different_email_routes_are_distinct(tmp_path, monkeypatch):
    """Same Agent may use distinct Email contacts for the same approved Port."""
    import json

    filename = str(tmp_path / "email_distinct_routes.sqlite3")
    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: ({"user_ref": "email-maker", "username": "uat.actor"}, {"MASTER_DATA"}),
    )
    db = connect()
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('agent','UAT-AGENT-001','{}','ACTIVE','now','now')"
    )
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('port','NLRTM','{}','ACTIVE','now','now')"
    )
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('configuration','EMAIL-CONTACT-A',?,'ACTIVE','now','now')",
        (json.dumps(_email()),),
    )
    db.close()
    pending = masterdata.create_change(
        masterdata.Change(
            domain="configuration",
            record_key="EMAIL-CONTACT-B",
            operation="CREATE",
            payload={**_email(), "email": "sales@example.org"},
            reason="Distinct Email contact",
        ),
        "maker",
    )
    assert pending["status"] == "PENDING"
    db = connect()
    assert db.execute(
        "SELECT COUNT(*) FROM md_change_requests WHERE record_key='EMAIL-CONTACT-B' AND status='PENDING'"
    ).fetchone()[0] == 1
    db.close()


def test_same_agent_same_email_different_ports_are_distinct(tmp_path, monkeypatch):
    """Agent Email identities include the Port, not only the email address."""
    import json

    filename = str(tmp_path / "email_distinct_ports.sqlite3")
    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: ({"user_ref": "email-maker", "username": "uat.actor"}, {"MASTER_DATA"}),
    )
    db = connect()
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('agent','UAT-AGENT-001','{}','ACTIVE','now','now')"
    )
    for port in ("NLRTM", "NLAMS"):
        db.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
            "VALUES ('port',?,'{}','ACTIVE','now','now')",
            (port,),
        )
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('configuration','EMAIL-PORT-RTM',?,'ACTIVE','now','now')",
        (json.dumps(_email("NLRTM")),),
    )
    db.close()
    request = masterdata.create_change(
        masterdata.Change(
            domain="configuration",
            record_key="EMAIL-PORT-AMS",
            operation="CREATE",
            payload=_email("NLAMS"),
            reason="Distinct approved Port",
        ),
        "maker",
    )
    assert request["status"] == "PENDING"
    db = connect()
    assert db.execute(
        "SELECT COUNT(*) FROM md_change_requests WHERE record_key='EMAIL-PORT-AMS' AND status='PENDING'"
    ).fetchone()[0] == 1
    db.close()


def test_checker_rejects_update_when_record_is_inactive(tmp_path, monkeypatch):
    """A stale UPDATE may not silently reactivate an inactive master record."""
    import json
    from fastapi import HTTPException

    filename = str(tmp_path / "inactive_update.sqlite3")

    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: ({"user_ref": "checker", "username": "uat.actor"}, {"MASTER_DATA_MANAGER"}),
    )
    db = connect()
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,version,created_at,updated_at) "
        "VALUES ('configuration','EMAIL-INACTIVE',?,'INACTIVE',1,'now','now')",
        (json.dumps(_email()),),
    )
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES ('MDC-INACTIVE-UPDATE','configuration','EMAIL-INACTIVE','UPDATE',?,'stale','maker','now',1)",
        (json.dumps({"email":"new@example.org"}),),
    )
    db.close()
    with pytest.raises(HTTPException) as error:
        masterdata.decide(
            "MDC-INACTIVE-UPDATE",
            masterdata.Decision(decision="APPROVE",comment="review"),
            "checker",
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "MASTER_RECORD_NOT_ACTIVE"
    db = connect()
    row = db.execute(
        "SELECT status,version,payload_json FROM md_records WHERE record_key='EMAIL-INACTIVE'"
    ).fetchone()
    assert (row["status"],row["version"],json.loads(row["payload_json"])["email"]) == (
        "INACTIVE",1,"ops@example.org"
    )
    db.close()


@pytest.mark.parametrize(
    "operation,status,code",
    [("DEACTIVATE","INACTIVE","MASTER_RECORD_NOT_ACTIVE"),
     ("REACTIVATE","ACTIVE","MASTER_RECORD_NOT_INACTIVE")],
)
def test_checker_rejects_invalid_state_transition(tmp_path,monkeypatch,operation,status,code):
    """Checker approval must respect the current master lifecycle state."""
    import json
    from fastapi import HTTPException

    filename=str(tmp_path / (operation + "_invalid_state.sqlite3"))
    def connect():
        db=sqlite3.connect(filename,isolation_level=None)
        db.row_factory=sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata,"connect",connect)
    monkeypatch.setattr(masterdata,"using_postgres",lambda:False)
    monkeypatch.setattr(
        masterdata,"authenticated_actor",
        lambda _db,_token:({"user_ref": "checker", "username": "uat.actor"},{"MASTER_DATA_MANAGER"}),
    )
    db=connect()
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,version,created_at,updated_at) "
        "VALUES ('configuration','EMAIL-STATE',?,?,1,'now','now')",
        (json.dumps(_email()),status),
    )
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES (?,?,?,?,?,'invalid transition','maker','now',1)",
        ("MDC-STATE-"+operation,"configuration","EMAIL-STATE",operation,json.dumps({})),
    )
    db.close()
    with pytest.raises(HTTPException) as error:
        masterdata.decide(
            "MDC-STATE-"+operation,
            masterdata.Decision(decision="APPROVE",comment="review"),
            "checker",
        )
    assert error.value.status_code==409
    assert error.value.detail["code"]==code
    db=connect()
    assert db.execute(
        "SELECT status FROM md_records WHERE record_key='EMAIL-STATE'"
    ).fetchone()["status"]==status
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref=?",
        ("MDC-STATE-"+operation,),
    ).fetchone()["status"]=="PENDING"
    db.close()


def test_checker_reactivation_revalidates_retired_port(tmp_path,monkeypatch):
    """An inactive Email cannot reactivate after its referenced Port retires."""
    import json
    from fastapi import HTTPException

    filename=str(tmp_path/"reactivate_retired_port.sqlite3")
    def connect():
        db=sqlite3.connect(filename,isolation_level=None)
        db.row_factory=sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata,"connect",connect)
    monkeypatch.setattr(masterdata,"using_postgres",lambda:False)
    monkeypatch.setattr(
        masterdata,"authenticated_actor",
        lambda _db,_token:({"user_ref": "checker", "username": "uat.actor"},{"MASTER_DATA_MANAGER"}),
    )
    db=connect()
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('agent','UAT-AGENT-001','{}','ACTIVE','now','now')"
    )
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('port','NLRTM','{}','INACTIVE','now','now')"
    )
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,version,created_at,updated_at) "
        "VALUES ('configuration','EMAIL-REACTIVATE',?,'INACTIVE',1,'now','now')",
        (json.dumps(_email()),),
    )
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES ('MDC-REACTIVATE-PORT','configuration','EMAIL-REACTIVATE','REACTIVATE','{}','review','maker','now',1)"
    )
    db.close()
    with pytest.raises(HTTPException) as err:
        masterdata.decide(
            "MDC-REACTIVATE-PORT",
            masterdata.Decision(decision="APPROVE",comment="review"),
            "checker",
        )
    assert err.value.status_code==422
    assert "AGENT_EMAIL_PORT_REFERENCE_NOT_ACTIVE" in err.value.detail["codes"]
    db=connect()
    assert db.execute(
        "SELECT status FROM md_records WHERE record_key='EMAIL-REACTIVATE'"
    ).fetchone()["status"]=="INACTIVE"
    db.close()


@pytest.mark.parametrize("operation", ["ACTIVATE", "REACTIVATE"])
def test_checker_reactivation_rejects_duplicate_active_email(tmp_path, monkeypatch, operation):
    """An inactive legacy email cannot revive over an approved active route."""
    import json
    from fastapi import HTTPException

    filename = str(tmp_path / "reactivation_collision.sqlite3")
    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: ({"user_ref": "checker", "username": "uat.actor"}, {"MASTER_DATA_MANAGER"}),
    )
    db = connect()
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('agent','UAT-AGENT-001','{}','ACTIVE','now','now')"
    )
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('port','NLRTM','{}','ACTIVE','now','now')"
    )
    for key, status in (("EMAIL-LIVE", "ACTIVE"), ("EMAIL-OLD", "INACTIVE")):
        db.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,version,created_at,updated_at) "
            "VALUES ('configuration',?,?,?,1,'now','now')",
            (key, json.dumps(_email()), status),
        )
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES ('MDC-EMAIL-COLLISION','configuration','EMAIL-OLD',?,'{}','review','maker','now',1)",
        (operation,)
    )
    db.close()

    with pytest.raises(HTTPException) as error:
        masterdata.decide(
            "MDC-EMAIL-COLLISION",
            masterdata.Decision(decision="APPROVE", comment="review"),
            "checker",
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "DUPLICATE_AGENT_RELATIONSHIP"
    db = connect()
    assert db.execute(
        "SELECT status FROM md_records WHERE record_key='EMAIL-OLD'"
    ).fetchone()["status"] == "INACTIVE"
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref='MDC-EMAIL-COLLISION'"
    ).fetchone()["status"] == "PENDING"
    db.close()


@pytest.mark.parametrize("operation", ["ACTIVATE", "REACTIVATE"])
def test_maker_activation_rejects_inactive_port_before_queue(tmp_path, monkeypatch, operation):
    """Retired Port references must fail at submission, not only checker approval."""
    import json
    from fastapi import HTTPException

    filename = str(tmp_path / ("maker_" + operation + ".sqlite3"))

    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: ({"user_ref": "maker", "username": "md.maker"}, {"MASTER_DATA"}),
    )

    db = connect()
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('agent','UAT-AGENT-001','{}','ACTIVE','now','now')"
    )
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
        "VALUES ('port','NLRTM','{}','INACTIVE','now','now')"
    )
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,version,created_at,updated_at) "
        "VALUES ('configuration','EMAIL-MAKER-REACT',?,'INACTIVE',1,'now','now')",
        (json.dumps(_email()),),
    )
    db.close()

    with pytest.raises(HTTPException) as error:
        masterdata.create_change(
            masterdata.Change(
                domain="configuration",
                record_key="EMAIL-MAKER-REACT",
                operation=operation,
                payload={},
                reason="Check retired Port",
            ),
            "maker",
        )
    assert error.value.status_code == 422
    assert "AGENT_EMAIL_PORT_REFERENCE_NOT_ACTIVE" in error.value.detail["codes"]
    db = connect()
    assert db.execute(
        "SELECT COUNT(*) FROM md_change_requests WHERE record_key='EMAIL-MAKER-REACT'"
    ).fetchone()[0] == 0
    db.close()


def test_checker_rejects_unknown_stored_operation(tmp_path, monkeypatch):
    """Legacy or corrupted pending requests cannot fall through to ACTIVATE."""
    import json
    from fastapi import HTTPException

    filename = str(tmp_path / "invalid_stored_operation.sqlite3")

    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: ({"user_ref": "checker", "username": "uat.actor"}, {"MASTER_DATA_MANAGER"}),
    )
    db = connect()
    db.execute(
        "INSERT INTO md_records(domain,record_key,payload_json,status,version,created_at,updated_at) "
        "VALUES ('configuration','EMAIL-INVALID-OP',?,'INACTIVE',1,'now','now')",
        (json.dumps(_email()),),
    )
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES ('MDC-INVALID-OP','configuration','EMAIL-INVALID-OP','DELETE','{}','invalid','maker','now',1)"
    )
    db.close()

    with pytest.raises(HTTPException) as error:
        masterdata.decide(
            "MDC-INVALID-OP",
            masterdata.Decision(decision="APPROVE", comment="review"),
            "checker",
        )
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "INVALID_MASTER_OPERATION"
    db = connect()
    assert db.execute(
        "SELECT status FROM md_records WHERE record_key='EMAIL-INVALID-OP'"
    ).fetchone()["status"] == "INACTIVE"
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref='MDC-INVALID-OP'"
    ).fetchone()["status"] == "PENDING"
    db.close()


def test_checker_can_reject_unknown_stored_operation(tmp_path, monkeypatch):
    """Malformed legacy requests remain rejectable by an independent checker."""
    from fastapi import HTTPException

    filename = str(tmp_path / "reject_invalid_stored_operation.sqlite3")

    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: ({"user_ref": "checker", "username": "uat.actor"}, {"MASTER_DATA_MANAGER"}),
    )
    db = connect()
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES ('MDC-INVALID-REJECT','configuration','BAD-REQUEST','DELETE','{}','invalid','maker','now',0)"
    )
    db.close()

    response = masterdata.decide(
        "MDC-INVALID-REJECT",
        masterdata.Decision(decision="REJECT", comment="Unsupported operation"),
        "checker",
    )
    assert response["status"] == "REJECTED"
    db = connect()
    row = db.execute(
        "SELECT status,checker FROM md_change_requests WHERE change_ref='MDC-INVALID-REJECT'"
    ).fetchone()
    assert row["status"] == "REJECTED"
    assert row["checker"] == "checker"
    db.close()


@pytest.mark.parametrize("maker,checker", [
    ("md.maker", "MD.MAKER"),
    ("md.maker ", " md.maker"),
    (" ", "md.checker"),
    ("md.maker", " "),
])
def test_checker_blocks_normalized_self_approval(tmp_path, monkeypatch, maker, checker):
    """Four-eyes approval must reject case and surrounding-space aliases."""
    from fastapi import HTTPException

    filename = str(tmp_path / "four_eyes_alias.sqlite3")

    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: ({"user_ref": checker, "username": "uat.actor"}, {"MASTER_DATA_MANAGER"}),
    )
    db = connect()
    db.execute(
        "INSERT INTO md_change_requests"
        "(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version) "
        "VALUES ('MDC-FOUR-EYES','configuration','B01-FOUR-EYES','CREATE','{}','test',?,'now',0)",
        (maker,),
    )
    db.close()

    with pytest.raises(HTTPException) as error:
        masterdata.decide(
            "MDC-FOUR-EYES",
            masterdata.Decision(decision="APPROVE", comment="must fail"),
            "checker",
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "FOUR_EYES_VIOLATION"
    db = connect()
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref='MDC-FOUR-EYES'"
    ).fetchone()["status"] == "PENDING"
    db.close()


@pytest.mark.parametrize("operation,prior_type,requested_type", [
    ("CREATE", None, " AGENT_EMAIL "),
    ("CREATE", None, "agent_office_assignment"),
    ("CREATE", None, "Agent_Network"),
    ("UPDATE", "OTHER", " AGENT_EMAIL "),
    ("UPDATE", "AGENT_EMAIL", "OTHER"),
    ("ACTIVATE", "AGENT_OFFICE_ASSIGNMENT", ""),
    ("DEACTIVATE", "AGENT_NETWORK", ""),
])
def test_isolated_checker_cannot_submit_agent_config(
    tmp_path, monkeypatch, operation, prior_type, requested_type
):
    """All B01 config lifecycle operations must honor separated UAT roles."""
    import json
    from fastapi import HTTPException

    filename = str(tmp_path / "checker_submission.sqlite3")
    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setenv("M3_B01_STRICT_MAKER_CHECKER", " TRUE ")
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC", "OFF")
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: (
            {"user_ref": "checker", "username": " MD.CHECKER "},
            {"MASTER_DATA", "MASTER_DATA_MANAGER"},
        ),
    )
    db = connect()
    if prior_type is not None:
        db.execute(
            "INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) "
            "VALUES ('configuration','B01-GUARD',?,'ACTIVE','now','now')",
            (json.dumps({"config_type": prior_type}),),
        )
    db.close()
    with pytest.raises(HTTPException) as error:
        masterdata.create_change(
            masterdata.Change(
                domain="configuration",
                record_key="B01-GUARD",
                operation=operation,
                payload={"config_type": requested_type} if requested_type else {},
                reason="Role separation regression",
            ),
            "valid",
        )
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "B01_UAT_CHECKER_CANNOT_SUBMIT_AGENT_CHANGE"
    db = connect()
    assert db.execute(
        "SELECT COUNT(*) FROM md_change_requests WHERE record_key='B01-GUARD'"
    ).fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("flow", ["maker", "checker"])
def test_missing_session_username_fails_closed(tmp_path, monkeypatch, flow):
    """Neither approval nor submission may proceed without a session username."""
    from fastapi import HTTPException

    filename = str(tmp_path / "no_username.sqlite3")
    def connect():
        db = sqlite3.connect(filename, isolation_level=None)
        db.row_factory = sqlite3.Row
        masterdata.init_schema(db)
        return db

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(
        masterdata, "authenticated_actor",
        lambda _db, _token: (
            {"user_ref": "uat-user"},
            {"MASTER_DATA", "MASTER_DATA_MANAGER"},
        ),
    )
    with pytest.raises(HTTPException) as error:
        if flow == "maker":
            masterdata.create_change(
                masterdata.Change(
                    domain="configuration", record_key="B01-NO-USERNAME",
                    operation="CREATE", payload={}, reason="Identity gate",
                ),
                "valid",
            )
        else:
            masterdata.decide(
                "MDC-NOT-FOUND",
                masterdata.Decision(decision="APPROVE"),
                "valid",
            )
    assert error.value.status_code == 403
    assert error.value.detail["code"] == (
        "MAKER_SESSION_USERNAME_REQUIRED" if flow == "maker"
        else "CHECKER_SESSION_USERNAME_REQUIRED"
    )
