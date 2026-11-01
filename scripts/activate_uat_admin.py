"""One-time, local-only activation for a pre-provisioned M3 UAT administrator.

Usage: from repository root with host-pinned M3 UAT environment:
    python scripts/activate_uat_admin.py

Never send passwords, hashes, or DATABASE_URL to chat or logs.
Does not seed users, create roles, alter existing memberships, or touch production.
"""
from __future__ import annotations

import getpass
import os
import sys
from urllib.parse import urlparse

from app import db
from app.admin import audit, phash, policy


def main() -> int:
    if os.getenv("M3_PRODUCTION_TRAFFIC", "OFF").upper() != "OFF":
        raise RuntimeError("Production traffic enabled; refused")
    if not db.using_postgres() or not db._uat_target_approved():
        raise RuntimeError("Host-pinned M3 Neon UAT connection required")
    url = urlparse(db.DATABASE_URL)
    if url.path != "/neondb" or url.username != "neondb_owner":
        raise RuntimeError("Expected dedicated M3 UAT neondb/neondb_owner")
    # Verify the exact M3 UAT Neon endpoint, not just any neon.tech host.
    expected = "ep-wild-sea-b49pzkl3-pooler.c-6.us-east-2.aws.neon.tech"
    if (url.hostname or "").lower() != expected:
        raise RuntimeError("Incorrect UAT Neon endpoint")
    c = db.connect()
    try:
        db.tx(c)
        c.execute("SELECT pg_advisory_xact_lock(84620317)")
        user = c.execute("SELECT id,user_ref,status,home_office_id,mfa_required FROM iam_users WHERE username='admin'").fetchone()
        if not user or user["status"] != "INACTIVE" or not user["mfa_required"]:
            raise RuntimeError("Expected INACTIVE MFA-enabled pre-provisioned admin")
        office = c.execute("SELECT office_code FROM iam_offices WHERE id=?", (user["home_office_id"],)).fetchone()
        if not office or office["office_code"] != "RTM":
            raise RuntimeError("Admin home office is not RTM")
        membership = c.execute("SELECT 1 FROM iam_office_membership WHERE user_id=? AND office_id=? AND status='ACTIVE'", (user["id"], user["home_office_id"])).fetchone()
        assignment = c.execute("""SELECT r.id FROM iam_user_roles ur JOIN iam_roles r ON r.id=ur.role_id
            WHERE ur.user_id=? AND r.role_code='SUPER_ADMIN' AND r.status='ACTIVE' AND ur.status='ACTIVE'""", (user["id"],)).fetchone()
        if not membership or not assignment:
            raise RuntimeError("Missing required RTM membership or SUPER_ADMIN assignment")
        permitted = c.execute("SELECT COUNT(*) AS n FROM iam_role_permissions WHERE role_id=? AND effect='ALLOW'", (assignment["id"],)).fetchone()["n"]
        catalog = c.execute("SELECT COUNT(*) AS n FROM iam_permissions").fetchone()["n"]
        if catalog != 68 or permitted != catalog:
            raise RuntimeError("SUPER_ADMIN permission catalogue or grants incomplete")
        minimum = max(12, int(policy(c, "password_min_length", "10")))
        print("Target verified: isolated M3 UAT, admin INACTIVE, RTM, 68 permissions.")
        pw = getpass.getpass("Create a private M3 UAT Admin password: ")
        confirmation = getpass.getpass("Confirm Admin password: ")
        if len(pw) < minimum or pw != confirmation:
            raise RuntimeError("Password confirmation failed or below minimum length")
        digest = phash(pw)
        del pw, confirmation
        c.execute("""UPDATE iam_users SET password_hash=?,status='ACTIVE',
                   failed_attempts=0,locked_until=NULL,version=version+1
                   WHERE id=? AND status='INACTIVE'""", (digest, user["id"]))
        audit(c, user["user_ref"], "UAT_ADMIN_ACTIVATE", "USER", user["user_ref"],
              before={"status": "INACTIVE"}, after={"status": "ACTIVE", "mfa_required": True},
              scope={"office": "RTM", "method": "local_private_prompt"})
        check = c.execute("SELECT status,mfa_required FROM iam_users WHERE id=?", (user["id"],)).fetchone()
        if check["status"] != "ACTIVE" or not check["mfa_required"]:
            raise RuntimeError("Activation verification failed")
        c.commit()
        print("Admin ACTIVE; RTM and SUPER_ADMIN assignments preserved. Test login and audit independently.")
        return 0
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("Activation not completed:", str(exc), file=sys.stderr)
        sys.exit(1)
