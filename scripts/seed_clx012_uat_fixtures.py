"""Non-destructive, explicit opt-in CLX012 fixture seeder for dedicated Neon UAT.

Default is dry-run. Apply requires --apply and M3_UAT_FIXTURE_WRITE=YES.
Never run on production or non-host-pinned PostgreSQL.
Existing fixture/business records are never updated or deleted.
"""
from __future__ import annotations
import argparse
import json
import os
from app import db
from app.business_day import CANONICAL_JOBS, OFFICE_SCOPE
from scripts.check_uat_clx012_fixtures import inspect

TABLES = ("vessels", "voyages", "customers", "agents", "bookings", "jobs",
          "workflow_states", "finance_states")
CODES = ("M3-UAT-SYN-CUST", "M3-UAT-SYN-AGENT")
VESSEL = "M3 UAT SYNTHETIC VESSEL"
VOYAGE = "M3-UAT-SYN-VOYAGE"
FIELDS = {"vessels": "name", "voyages": "voyage_no", "customers": "code",
          "agents": "code", "bookings": "booking_ref", "jobs": "job_ref"}

def _row(conn, table, field, value):
    return conn.execute(f"SELECT id FROM {table} WHERE {field}=?", (value,)).fetchone()

def _next(conn, table):
    return int(conn.execute(f"SELECT COALESCE(MAX(id),0)+1 AS id FROM {table}").fetchone()["id"])

def _ensure(conn, table, field, value, values, *, apply):
    row = _row(conn, table, field, value)
    if row:
        return int(row["id"]), False
    if not apply:
        return None, True
    record = {"id": _next(conn, table), **values}
    keys = list(record)
    conn.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join(['?'] * len(keys))})",
                 tuple(record[k] for k in keys))
    return record["id"], True

def seed(conn, *, apply=False):
    present = {table: db.table_exists(conn, table) for table in TABLES}
    if not all(present.values()):
        raise RuntimeError("Missing fixture schema: " + ", ".join(t for t, ok in present.items() if not ok))
    existing = [j for j in CANONICAL_JOBS if _row(conn, "jobs", "job_ref", j)]
    if existing and len(existing) != len(CANONICAL_JOBS):
        raise RuntimeError("Partial canonical job set: manual review required; no changes made")
    if existing:
        outcome = inspect(conn)
        if not outcome["ready_for_clx012"]:
            raise RuntimeError("Existing canonical records incomplete: manual review required")
        return {"mode": "no-op", "jobs_present": len(existing), "ready_for_clx012": True}
    if not apply:
        return {"mode": "dry-run", "would_create_jobs": list(CANONICAL_JOBS),
                "preserves_existing_records": True}
    vessel, _ = _ensure(conn, "vessels", "name", VESSEL, {"name": VESSEL}, apply=True)
    voyage, _ = _ensure(conn, "voyages", "voyage_no", VOYAGE,
                         {"voyage_no": VOYAGE, "vessel_id": vessel}, apply=True)
    customer, _ = _ensure(conn, "customers", "code", CODES[0],
                           {"code": CODES[0], "name": "M3 Synthetic UAT Customer"}, apply=True)
    agent, _ = _ensure(conn, "agents", "code", CODES[1],
                        {"code": CODES[1], "name": "M3 Synthetic UAT Agent"}, apply=True)
    for job in CANONICAL_JOBS:
        office, country = OFFICE_SCOPE[job]
        booking, _ = _ensure(conn, "bookings", "booking_ref", "M3-UAT-BKG-" + job,
                              {"booking_ref": "M3-UAT-BKG-" + job,
                               "customer_id": customer, "agent_id": agent,
                               "voyage_id": voyage, "pol": "NLRTM", "pod": "AEDXB"}, apply=True)
        job_id, _ = _ensure(conn, "jobs", "job_ref", job,
                             {"job_ref": job, "booking_id": booking,
                              "customer_id": customer, "agent_id": agent, "voyage_id": voyage,
                              "pol": "NLRTM", "pod": "AEDXB", "operational_status": "SYNTHETIC_UAT",
                              "office_code": office, "country_code": country, "version": 1}, apply=True)
        for table, vals in (
            ("workflow_states", {"documentation_status": "PENDING", "vgm_status": "PENDING",
                                  "customs_status": "PENDING", "transshipment_status": "PENDING",
                                  "release_status": "HOLD", "closed": 0, "version": 1}),
            ("finance_states", {"payment_status": "UNPAID", "currency": "EUR",
                                "outstanding": 0, "credit_hold": 0}),
        ):
            if conn.execute(f"SELECT 1 FROM {table} WHERE job_id=?", (job_id,)).fetchone():
                raise RuntimeError(f"Refusing to overwrite existing {table} for job {job}")
            data = {"id": _next(conn, table), "job_id": job_id, **vals}
            cols = list(data)
            conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                         tuple(data[col] for col in cols))
    outcome = inspect(conn)
    if not outcome["ready_for_clx012"]:
        raise RuntimeError("Fixture linkage verification failed")
    return {"mode": "applied", "created_jobs": len(CANONICAL_JOBS), "ready_for_clx012": True}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not db.using_postgres() or not db._uat_target_approved() or os.getenv("M3_PRODUCTION_TRAFFIC","OFF") != "OFF":
        raise RuntimeError("Dedicated host-pinned Neon UAT only; production disabled")
    if args.apply and os.getenv("M3_UAT_FIXTURE_WRITE") != "YES":
        raise RuntimeError("Explicit M3_UAT_FIXTURE_WRITE=YES required")
    conn = db.connect()
    try:
        if args.apply:
            db.tx(conn)
            conn.execute("SELECT pg_advisory_xact_lock(84620318)")
        result = seed(conn, apply=args.apply)
        if args.apply:
            conn.commit()
        print(json.dumps(result, indent=2))
    except Exception:
        if args.apply:
            conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
