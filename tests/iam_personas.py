"""Test-only IAM personas, isolated to pytest-created SQLite databases.

Call after seed_run(), admin_seed_run(), and masterdata_seed_run().
Never import this module from app/ or use it against a production database.
"""
import uuid

from app import db
from app.admin import Login, login, now, phash


def persona_session(role="AGENT", agent_code="CLX-AGT-SIN", office_code=None):
    """Provision one real IAM identity and authenticate through the login endpoint."""
    if db.using_postgres():
        raise RuntimeError("TEST_PERSONA_POSTGRES_FORBIDDEN")
    allowed = {"AGENT", "VIEWER", "OPS", "AUDITOR", "FINANCE", "TREASURY", "GL_ACCOUNTANT"}
    if role not in allowed:
        raise ValueError("UNSUPPORTED_TEST_ROLE")
    suffix = uuid.uuid4().hex[:16]
    user = "test." + role.lower() + "." + suffix
    password = "Test!"+suffix+"Aa1"
    office_code = office_code or ("AGT-" + agent_code if role == "AGENT" else "RTM")
    c = db.connect()
    try:
        office = c.execute("SELECT id FROM iam_offices WHERE office_code=? AND status='ACTIVE'", (office_code,)).fetchone()
        if not office and role == "AGENT":
            base = c.execute("SELECT organization_id,country_id FROM iam_offices WHERE office_code='SIN' AND status='ACTIVE'").fetchone()
            if not base:
                raise RuntimeError("TEST_AGENT_OFFICE_REFERENCE_MISSING")
            c.execute("INSERT INTO iam_offices(office_code,name,organization_id,country_id,timezone,status) VALUES(?,?,?,?,?,'ACTIVE')",
                      (office_code, "Test scoped agent office", base["organization_id"], base["country_id"], "Asia/Singapore"))
            office = c.execute("SELECT id FROM iam_offices WHERE office_code=?", (office_code,)).fetchone()
        if not office:
            raise RuntimeError("TEST_OFFICE_NOT_FOUND")
        rr = c.execute("SELECT id FROM iam_roles WHERE role_code=? AND status='ACTIVE'", (role,)).fetchone()
        if not rr:
            raise RuntimeError("TEST_ROLE_NOT_FOUND")
        c.execute("INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required) VALUES(?,?,?,?,?,?,1)",
                  ("USR-TEST-"+suffix,user,"Test "+role,user+"@m3.test",phash(password),office["id"]))
        uid = c.execute("SELECT id FROM iam_users WHERE username=?", (user,)).fetchone()["id"]
        c.execute("INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,status,assigned_by) VALUES(?,?,?,?,'ACTIVE','pytest')",
                  (uid,rr["id"],office["id"],now()))
        c.execute("INSERT INTO iam_office_membership(user_id,office_id,membership_type,status) VALUES(?,?,'PRIMARY','ACTIVE')",
                  (uid,office["id"]))
        if role == "AGENT":
            c.execute("INSERT INTO iam_party_access(user_id,party_type,party_key,access_level,status) VALUES(?,'AGENT',?,'VIEW','ACTIVE')",
                      (uid,agent_code))
        c.close()
    except Exception:
        c.close()
        raise
    result = login(Login(username=user,password=password,mfa_code="123456"))
    if role not in result["roles"]:
        raise AssertionError("TEST_IAM_ROLE_MISMATCH")
    return result["session_token"]
