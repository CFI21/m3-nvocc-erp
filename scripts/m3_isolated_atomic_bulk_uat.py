#!/usr/bin/env python3
"""M3 isolated atomic-bulk UAT. Secrets come ONLY from environment variables.

Default: authentication/read-only checks. Use --submit only on approved isolated
UAT with disposable data; NEVER against production or ANCLINE.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

API = "https://m3-dev-sprint2-isolated-api.onrender.com"
def call(path, *, method="GET", token=None, session=None, body=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["X-M3-UAT-Token"] = token
    if session:
        headers["X-M3-Session"] = session
    if body is not None:
        headers["Content-Type"] = "application/json"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            payload = json.load(error)
        except Exception:
            payload = {"detail": "HTTP error"}
        return error.code, payload

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def login(username, password, mfa, token):
    status, info = call("/api/admin/auth/login", method="POST", token=token,
        body={"username":username, "password":password, "mfa_code":mfa})
    require(status == 200 and isinstance(info.get("session_token"), str),
            f"{username} login blocked: HTTP {status} {info.get('detail')}")
    session = info["session_token"]
    status, who = call("/api/admin/auth/whoami", token=token, session=session)
    require(status == 200 and who.get("username") == username, "Identity check failed")
    return session, who.get("roles", [])

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--submit", action="store_true", help="Create disposable pending changes in isolated UAT")
    args = parser.parse_args()
    require(urlparse(API).hostname == "m3-dev-sprint2-isolated-api.onrender.com", "Wrong target")
    token = os.getenv("M3_UAT_TOKEN")
    require(bool(token), "M3_UAT_TOKEN required")
    status, health = call("/api/v1/health")
    require(status == 200, f"Health check failed: {status}")
    require(health.get("production_promoted") is False and health.get("sandbox_only") is True,
            "Cannot verify isolated non-production runtime")
    status, readiness = call("/api/clx016/readiness")
    require(status == 200 and readiness.get("production_traffic") == "OFF" and readiness.get("live_providers") == "OFF" and readiness.get("real_money") == "OFF",
            "Isolated runtime safety flags cannot be verified")
    maker_session = checker_session = None
    try:
        maker_session, maker_roles = login("md.maker", os.environ["M3_MAKER_PASSWORD"], os.environ["M3_MAKER_MFA"], token)
        checker_session, checker_roles = login("md.checker", os.environ["M3_CHECKER_PASSWORD"], os.environ["M3_CHECKER_MFA"], token)
        require("MASTER_DATA" in maker_roles and "SUPER_ADMIN" not in maker_roles, "Maker role mismatch")
        require("MASTER_DATA_MANAGER" in checker_roles and "SUPER_ADMIN" not in checker_roles, "Checker role mismatch")
        print("PASS: isolated health and distinct Maker/Checker sessions")
        if not args.submit:
            print("HOLD: no records submitted. --submit explicitly required.")
            return 0
        suffix = uuid.uuid4().hex[:12].upper()
        key = "m3-uat-" + suffix.lower()
        body = {"domain":"agent","rows":[
            {"record_key":"UAT-BULK-"+suffix+"-A","operation":"CREATE","payload":{"name":"UAT Bulk "+suffix+" A"},"reason":"Isolated disposable acceptance test"},
            {"record_key":"UAT-BULK-"+suffix+"-B","operation":"CREATE","payload":{"name":"UAT Bulk "+suffix+" B"},"reason":"Isolated disposable acceptance test"}]}
        def submit(payload):
            headers = {"X-M3-UAT-Token":token,"X-M3-Session":maker_session,
                       "Content-Type":"application/json","Idempotency-Key":key}
            request=urllib.request.Request(API+"/api/masterdata/bulk/submit-atomic",
                data=json.dumps(payload).encode(),headers=headers,method="POST")
            try:
                with urllib.request.urlopen(request,timeout=25) as response:
                    return response.status,json.load(response)
            except urllib.error.HTTPError as error:
                return error.code,json.load(error)
        code, first = submit(body)
        require(code == 200 and first.get("atomic") is True and first.get("pending")==2,
                f"Atomic submit failed: HTTP {code} {first}")
        code, replay = submit(body)
        require(code == 200 and replay == first, f"Replay failed: HTTP {code}")
        recovery_status, recovery = call("/api/masterdata/bulk/submit-atomic/recovery?idempotency_key="+key,
            token=token,session=maker_session)
        require(recovery_status == 200 and recovery == first, "Recovery mismatch")
        changed = json.loads(json.dumps(body))
        changed["rows"][0]["payload"]["name"] += " altered"
        code, conflict = submit(changed)
        require(code == 409, f"Conflicting replay should be 409, got {code}")
        print("PASS: atomic submission, idempotent replay, recovery, payload conflict")
        print("Pending change refs:", [r.get("change_ref") for r in first["rows"]])
        print("HOLD: independent Checker approval and rollback-on-late-failure require separate governed tests")
        return 0
    finally:
        for session in (maker_session, checker_session):
            if session:
                try:
                    call("/api/admin/auth/logout",method="POST",token=token,session=session)
                except Exception:
                    pass
if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, KeyError) as error:
        print("FAIL:",str(error),file=sys.stderr)
        sys.exit(1)
