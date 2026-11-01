"""Compare two isolated Neon UAT branches without exposing credentials or modifying data.

Set M3_UAT_SOURCE_DATABASE_URL and M3_UAT_RESTORE_DATABASE_URL in the local
environment (never in command arguments), then run:
    python scripts/verify_neon_uat_restore.py

Requires psycopg and two distinct host-pinned Neon UAT database URLs.
This verifies selected record counts, canonical identities and audit structures;
it does not certify application rollback or replace a full disaster-recovery drill.
"""
from __future__ import annotations
import json
import os
from urllib.parse import urlparse

TABLES = (
    "jobs", "bookings", "customers", "agents", "vessels", "voyages",
    "workflow_states", "finance_states", "iam_users", "iam_roles",
    "clx012_runs", "clx012_stage_events", "clx012_exceptions",
    "clx013_schema_versions", "clx013_request_log", "clx013_runtime_events",
)
CANONICAL = ("50001", "50002", "50003", "50004", "50005")

def validate_pair(source, restored):
    a, b = urlparse(source), urlparse(restored)
    for value in (a,b):
        if value.scheme not in ("postgres", "postgresql") or not value.hostname:
            raise ValueError("Both connections must be PostgreSQL URLs")
        if not value.hostname.endswith(".neon.tech"):
            raise ValueError("Only isolated Neon UAT hosts are allowed")
    if source == restored or a.hostname == b.hostname:
        raise ValueError("Source and restored branches must use distinct endpoints")
    if os.getenv("M3_UAT_PRESERVE_DATA") != "1" or os.getenv("M3_PRODUCTION_TRAFFIC", "OFF") != "OFF":
        raise ValueError("UAT preservation and production-traffic OFF are required")

def inspect(conn):
    with conn.cursor() as c:
        result = {}
        for table in TABLES:
            c.execute("SELECT to_regclass(%s)", ("public." + table,))
            if c.fetchone()[0] is None:
                raise ValueError("Required table missing: " + table)
            c.execute("SELECT COUNT(*) FROM " + table)
            result[table] = c.fetchone()[0]
        c.execute("SELECT job_ref FROM jobs WHERE job_ref = ANY(%s) ORDER BY job_ref", (list(CANONICAL),))
        result["canonical_jobs"] = [row[0] for row in c.fetchall()]
        c.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'clx013_%_no_%' ORDER BY tgname")
        result["immutable_clx013_triggers"] = [row[0] for row in c.fetchall()]
        return result

def main():
    source = os.environ["M3_UAT_SOURCE_DATABASE_URL"]
    restored = os.environ["M3_UAT_RESTORE_DATABASE_URL"]
    validate_pair(source, restored)
    import psycopg
    # Both sessions are read-only and use independent connections.
    with psycopg.connect(source, options="-c default_transaction_read_only=on") as original, \
         psycopg.connect(restored, options="-c default_transaction_read_only=on") as copy:
        a,b = inspect(original), inspect(copy)
    differences = {key: {"source": a[key], "restored": b[key]}
                   for key in a if a[key] != b[key]}
    passed = not differences and a["canonical_jobs"] == list(CANONICAL) and len(a["immutable_clx013_triggers"]) >= 4
    print(json.dumps({"match": passed, "differences": differences,
                      "canonical_jobs_verified": a["canonical_jobs"] == list(CANONICAL),
                      "audit_triggers_verified": len(a["immutable_clx013_triggers"]) >= 4}, indent=2))
    if not passed:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
