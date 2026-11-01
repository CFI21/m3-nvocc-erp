"""Read-only CLX012 synthetic fixture preflight for the isolated M3 Neon UAT database.

Run locally with the existing host-pinned M3 UAT environment:
    python scripts/check_uat_clx012_fixtures.py
Never prints credentials and never inserts, updates or deletes records.
"""
from __future__ import annotations

import json
import sys
from app import db
from app.business_day import CANONICAL_JOBS

REQUIRED = (
    "jobs", "bookings", "customers", "agents", "workflow_states",
    "finance_states", "clx012_runs", "clx012_stage_events",
    "clx012_exceptions", "clx012_action_queue", "clx012_events",
)


def inspect(conn):
    missing_tables = [name for name in REQUIRED if not db.table_exists(conn, name)]
    result = {"target": "isolated-neon-uat", "missing_tables": missing_tables,
              "expected_jobs": list(CANONICAL_JOBS), "missing_jobs": [],
              "incomplete_jobs": [], "ready_for_clx012": False}
    if missing_tables:
        return result
    for job_ref in CANONICAL_JOBS:
        row = conn.execute(
            """SELECT j.job_ref, b.id AS booking_id, c.id AS customer_id,
                      a.id AS agent_id, w.id AS workflow_id, f.id AS finance_id
               FROM jobs j
               LEFT JOIN bookings b ON b.id=j.booking_id
               LEFT JOIN customers c ON c.id=j.customer_id
               LEFT JOIN agents a ON a.id=j.agent_id
               LEFT JOIN workflow_states w ON w.job_id=j.id
               LEFT JOIN finance_states f ON f.job_id=j.id
               WHERE j.job_ref=?""", (job_ref,)
        ).fetchone()
        if not row:
            result["missing_jobs"].append(job_ref)
            continue
        absent = [field for field in ("booking_id", "customer_id", "agent_id",
                                      "workflow_id", "finance_id") if row[field] is None]
        if absent:
            result["incomplete_jobs"].append({"job_ref": job_ref, "missing": absent})
    result["ready_for_clx012"] = not result["missing_jobs"] and not result["incomplete_jobs"]
    return result


def main():
    if not db.using_postgres() or not db._uat_target_approved():
        raise RuntimeError("Host-pinned isolated Neon UAT PostgreSQL required")
    conn = db.connect()
    try:
        print(json.dumps(inspect(conn), indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Preflight failed:", type(exc).__name__, file=sys.stderr)
        sys.exit(1)
