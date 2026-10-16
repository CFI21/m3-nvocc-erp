"""B01 Email reference gate: independent read-only disposable DB checks.

No Neon data, IAM changes, port seeding in deployed environments, or production calls.
"""
import sqlite3

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
            return {"user_ref": "email-maker"}, {"MASTER_DATA"}
        if token == "checker":
            return {"user_ref": "email-checker"}, {"MASTER_DATA_MANAGER"}
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
            {"user_ref": "email-maker" if token == "maker" else "email-checker"},
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
            {"user_ref": "maker" if token == "maker" else "checker"},
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
