"""Fail-closed isolated UAT startup. Never drop databases or reseed stored records.

Not enabled in Render until a complete PostgreSQL schema has been migrated and
validated in the dedicated preview database. Production startup is unchanged.
"""
import os
from . import db

REQUIRED_TABLES = (
    "iam_users", "iam_sessions", "md_records", "md_change_requests",
    "md_audit", "agents", "bookings", "bills",
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
        return {"database": "isolated-neon-uat", "schema": "ready",
                "pending_approvals": pending["n"], "master_agents": agents["n"]}
    finally:
        conn.close()

if __name__ == "__main__":
    print(ensure_uat_ready())
