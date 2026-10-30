"""Fail-closed isolated UAT startup. Never drop databases or reseed stored records.

Not enabled in Render until a complete PostgreSQL schema has been migrated and
validated in the dedicated preview database. Production startup is unchanged.
"""
import os
from . import db

REQUIRED_TABLES = (
    "iam_users", "iam_sessions", "md_records", "md_change_requests",
    "md_audit", "iam_user_roles", "iam_roles", "agents", "bookings", "bills",
)

def ensure_uat_ready():
    if os.getenv("M3_UAT_PRESERVE_DATA") != "1":
        raise RuntimeError("UAT_PRESERVATION_REQUIRED")
    if not db.DATABASE_URL:
        # SQLite can be suitable for offline tests, not persistent Render hosting.
        raise RuntimeError("UAT_DURABLE_DATABASE_REQUIRED")
    if not db._uat_target_approved():
        raise RuntimeError("UAT_NEON_TARGET_NOT_APPROVED")
    conn = db.connect()
    try:
        absent = [t for t in REQUIRED_TABLES if not db.table_exists(conn, t)]
        if absent:
            raise RuntimeError("UAT_SCHEMA_MIGRATION_REQUIRED: " + ", ".join(absent))
        pending = conn.execute("SELECT COUNT(*) AS n FROM md_change_requests WHERE status='PENDING'").fetchone()
        agents = conn.execute("SELECT COUNT(*) AS n FROM md_records WHERE domain='agent'").fetchone()
        identities = conn.execute(
            "SELECT COUNT(*) AS n FROM iam_users WHERE username IN ('md.maker','md.checker')"
        ).fetchone()
        if identities["n"] != 2:
            raise RuntimeError("UAT_IAM_INITIALIZATION_REQUIRED: independent maker/checker accounts missing")
        # Match runtime roles_for() authorization: user, role and grant must
        # all be active, and the assignment must be within its effective dates.
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        role_rows = conn.execute(
            "SELECT u.username, r.role_code "
            "FROM iam_users u JOIN iam_user_roles ur ON ur.user_id=u.id "
            "JOIN iam_roles r ON r.id=ur.role_id "
            "WHERE u.username IN ('md.maker','md.checker') "
            "AND u.status='ACTIVE' AND r.status='ACTIVE' AND ur.status='ACTIVE' "
            "AND ur.valid_from<=? AND (ur.valid_to IS NULL OR ur.valid_to>=?)",
            (ts, ts),
        ).fetchall()
        assigned = {(row["username"], row["role_code"]) for row in role_rows}
        required = {("md.maker", "MASTER_DATA"), ("md.checker", "MASTER_DATA_MANAGER")}
        if assigned != required:
            raise RuntimeError("UAT_IAM_ROLE_SEPARATION_REQUIRED: active independent maker/checker roles missing or invalid")
        return {"database": "isolated-neon-uat", "schema": "ready",
                "pending_approvals": pending["n"], "master_agents": agents["n"]}
    finally:
        conn.close()

if __name__ == "__main__":
    print(ensure_uat_ready())
