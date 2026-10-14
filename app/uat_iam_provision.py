"""One-time isolated Neon UAT IAM provisioning; never seeds known passwords.

Run only after schema validation with M3_UAT_PROVISION_IAM=YES and unique
M3_UAT_MAKER_PASSWORD / M3_UAT_CHECKER_PASSWORD environment secrets.
Never import or invoke this module in normal API startup.
"""
import os
import secrets
from . import db
from .admin import phash

def provision():
    if os.getenv("M3_UAT_PROVISION_IAM") != "YES" or not db._uat_target_approved():
        raise RuntimeError("UAT_IAM_PROVISION_NOT_AUTHORIZED")
    passwords = [os.getenv("M3_UAT_MAKER_PASSWORD", ""), os.getenv("M3_UAT_CHECKER_PASSWORD", "")]
    if any(len(p) < 16 for p in passwords) or passwords[0] == passwords[1]:
        raise RuntimeError("UAT_IAM_UNIQUE_STRONG_PASSWORDS_REQUIRED")
    if any(p in ("Maker123!", "Checker123!", "Admin123!") for p in passwords):
        raise RuntimeError("UAT_IAM_KNOWN_PASSWORD_REJECTED")
    c = db.connect()
    try:
        if c.execute("SELECT 1 FROM iam_users LIMIT 1").fetchone():
            raise RuntimeError("UAT_IAM_ALREADY_INITIALIZED")
        c.execute("BEGIN")
        c.execute("INSERT INTO iam_organizations(org_code,name) VALUES(?,?)", ("M3-UAT", "M3 Isolated UAT"))
        c.execute("INSERT INTO iam_countries(country_code,name) VALUES(?,?)", ("NL", "Netherlands"))
        org = c.execute("SELECT id FROM iam_organizations WHERE org_code=?", ("M3-UAT",)).fetchone()["id"]
        country = c.execute("SELECT id FROM iam_countries WHERE country_code=?", ("NL",)).fetchone()["id"]
        c.execute("INSERT INTO iam_offices(office_code,name,organization_id,country_id,timezone) VALUES(?,?,?,?,?)",
                  ("UAT-RTM", "Rotterdam UAT Office", org, country, "Europe/Amsterdam"))
        office = c.execute("SELECT id FROM iam_offices WHERE office_code=?", ("UAT-RTM",)).fetchone()["id"]
        for code, title in (("MASTER_DATA", "Master Data Maker"), ("MASTER_DATA_MANAGER", "Master Data Checker")):
            c.execute("INSERT INTO iam_roles(role_code,name) VALUES(?,?)", (code, title))
        for ref, user, title, role, password in (
            ("UAT-MAKER-001", "md.maker", "Independent UAT Maker", "MASTER_DATA", passwords[0]),
            ("UAT-CHECKER-002", "md.checker", "Independent UAT Checker", "MASTER_DATA_MANAGER", passwords[1]),
        ):
            c.execute("INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required) VALUES(?,?,?,?,?,?,?)",
                      (ref,user,title,user+"@m3-uat.invalid",phash(password),office,1))
            uid = c.execute("SELECT id FROM iam_users WHERE username=?", (user,)).fetchone()["id"]
            rid = c.execute("SELECT id FROM iam_roles WHERE role_code=?", (role,)).fetchone()["id"]
            c.execute("INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,assigned_by) VALUES(?,?,?,?,?)",
                      (uid,rid,office,"2026-01-01T00:00:00+00:00","uat-provisioner"))
        c.execute("COMMIT")
        return {"created": 2, "roles": ["MASTER_DATA", "MASTER_DATA_MANAGER"]}
    except Exception:
        c.execute("ROLLBACK")
        raise
    finally:
        c.close()

if __name__ == "__main__":
    print(provision())
