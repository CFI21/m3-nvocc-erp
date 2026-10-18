"""Test-only IAM personas, isolated to pytest-created SQLite databases.

Call after seed_run(), admin_seed_run(), and masterdata_seed_run().
Never import this module from app/ or use it against a production database.
"""
import uuid
import json

from app import db
from app.admin import Login, login, now, phash


def persona_session(role="AGENT", agent_code="CLX-AGT-SIN", office_code=None):
    """Provision one real IAM identity and authenticate through the login endpoint."""
    if db.using_postgres():
        raise RuntimeError("TEST_PERSONA_POSTGRES_FORBIDDEN")
    allowed = {"AGENT", "VIEWER", "OPS", "AUDITOR", "FINANCE", "TREASURY", "GL_ACCOUNTANT", "DOCS"}
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
        if role == "AGENT":
            # Equipment IAM requires a branch in the member's home office.
            existing_branch = c.execute(
                "SELECT id FROM iam_branches WHERE office_id=? ORDER BY id LIMIT 1",
                (office["id"],)
            ).fetchone()
            if not existing_branch:
                c.execute(
                    "INSERT INTO iam_branches(branch_code,name,office_id,status) VALUES(?,?,?,'ACTIVE')",
                    ("TEST-" + suffix.upper(), "Test agent branch", office["id"])
                )
        if role == "DOCS":
            c.execute("INSERT OR IGNORE INTO iam_roles(role_code,name,status,sensitive) VALUES('DOCS','Test Documents','ACTIVE',0)")
            doc_role = c.execute("SELECT id FROM iam_roles WHERE role_code='DOCS'").fetchone()["id"]
            for perm in c.execute("SELECT id FROM iam_permissions WHERE module='agent-tasks' AND action IN ('view','create','edit')").fetchall():
                c.execute("INSERT OR IGNORE INTO iam_role_permissions(role_id,permission_id,effect) VALUES(?,?,'ALLOW')",
                          (doc_role,perm["id"]))
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
        if role == "AGENT" and office_code == "RTM":
            # A test-only governed assignment links the seeded job's RTM office to
            # the real AGENT IAM membership. The production authorization logic
            # still performs both office membership and agent grant checks.
            key = "TEST-ASSIGN-" + suffix
            payload = json.dumps({"config_type": "AGENT_OFFICE_ASSIGNMENT",
                                  "agent_code": agent_code, "office_code": office_code})
            ts = now()
            c.execute(
                "INSERT INTO md_records(domain,record_key,display_name,payload_json,status,version,"
                "approved_by,approved_at,created_at,updated_at) "
                "VALUES('configuration',?,?,?,'ACTIVE',1,'pytest',?,?,?)",
                (key, "Isolated agent office assignment", payload, ts, ts, ts))
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
