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
