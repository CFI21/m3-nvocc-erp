"""Read-only inventory of prerequisite records for CLX-012 synthetic UAT.

Run only against an approved isolated Neon target:
    python scripts/inspect_uat_fixture_dependencies.py
No INSERT/UPDATE/DELETE, no secrets printed.
"""
from __future__ import annotations
import json
import sys
from app import db

TABLES = ("vessels", "voyages", "customers", "agents", "bookings",
          "jobs", "workflow_states", "finance_states", "workflow_holds")

def inspect(conn):
    counts = {}
    for name in TABLES:
        if not db.table_exists(conn, name):
            counts[name] = None
            continue
        counts[name] = int(conn.execute("SELECT COUNT(*) AS n FROM " + name).fetchone()["n"])
    missing = [name for name, count in counts.items() if count == 0 or count is None]
    return {
        "environment": "isolated-neon-uat",
        "row_counts": counts,
        "missing_or_empty_prerequisites": missing,
        "can_generate_business_day": not missing,
        "write_operations": 0,
    }

def main():
    if not db.using_postgres() or not db._uat_target_approved():
        raise RuntimeError("Approved host-pinned isolated PostgreSQL UAT required")
    conn = db.connect()
    try:
        print(json.dumps(inspect(conn), indent=2))
    finally:
        conn.close()

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Preflight failed:", type(error).__name__, file=sys.stderr)
        sys.exit(1)
