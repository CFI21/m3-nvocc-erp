"""Read-only diagnostics for isolated M3 UAT account sign-in failures.

Run only in the approved isolated UAT environment:
    python -m app.uat_iam_diagnostics
Never prints passwords, password hashes, session tokens or database URLs.
"""
import json
import os

from . import db

ACCOUNTS = ("admin", "md.maker", "md.checker")


def inspect_accounts():
    if os.getenv("M3_UAT_PRESERVE_DATA") != "1" or not db.DATABASE_URL or not db._uat_target_approved():
        raise RuntimeError("ISOLATED_UAT_DATABASE_REQUIRED")
    conn = db.connect()
    try:
        if not db.table_exists(conn, "iam_users"):
            raise RuntimeError("UAT_IAM_SCHEMA_MISSING")
        results = []
        for username in ACCOUNTS:
            row = conn.execute(
                "SELECT user_ref,status,mfa_required,failed_attempts,"
                "CASE WHEN password_hash IS NOT NULL AND LENGTH(password_hash)>0 "
                "THEN 1 ELSE 0 END AS password_present "
                "FROM iam_users WHERE username=?",
                (username,),
            ).fetchone()
            results.append({
                "username": username,
                "exists": row is not None,
                "user_ref": row["user_ref"] if row else None,
                "status": row["status"] if row else None,
                "mfa_required": bool(row["mfa_required"]) if row else None,
                "failed_attempts": row["failed_attempts"] if row else None,
                "password_provisioned": bool(row["password_present"]) if row else None,
            })
        return {"environment": "isolated-uat", "accounts": results}
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(inspect_accounts(), sort_keys=True))
