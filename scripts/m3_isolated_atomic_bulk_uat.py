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
import re
import urllib.request
import uuid
from urllib.parse import urlparse

API = "https://m3-dev-sprint2-isolated-api.onrender.com"
def call(path, *, method="GET", token=None, session=None, body=None, extra_headers=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["X-M3-UAT-Token"] = token
    if session:
        headers["X-M3-Session"] = session
    if body is not None:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            try:
                return response.status, json.load(response)
            except (ValueError, UnicodeError):
                return response.status, {}
    except urllib.error.HTTPError as error:
        try:
            payload = json.load(error)
        except Exception:
            payload = {}
        # Preserve HTTP status and a safe gateway diagnostic, without echoing
        # response bodies, headers, credentials or session tokens.
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("_http_status", error.code)
        return error.code, payload

def normalize_uat_token(raw):
    """Strip only accidental copy/paste whitespace; reject all other controls."""
    if not raw:
        raise RuntimeError("M3_UAT_TOKEN required")
    allowed_whitespace = " \t\r\n\v\f"
    if any((ord(ch) < 33 or ord(ch) > 126) and ch not in allowed_whitespace for ch in raw):
        raise RuntimeError("M3_UAT_TOKEN has unsafe non-ASCII or control characters")
    cleaned = "".join(ch for ch in raw if ch not in allowed_whitespace)
    if not cleaned:
        raise RuntimeError("M3_UAT_TOKEN required")
    if cleaned != raw:
        print("INFO: normalized whitespace in UAT token (value not displayed)")
    return cleaned

def safe_error(status, payload):
    # Do not echo arbitrary gateway HTML, submitted credentials, or session tokens.
    if isinstance(payload, dict):
        detail = payload.get("detail", "")
        if isinstance(detail, dict):
            code = detail.get("code", "")
            if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{2,80}", code):
                return f"HTTP {status} {code}"
    return f"HTTP {status}; non-M3 or unstructured response (check gateway/proxy)"

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def login(username, password, mfa, token):
    status, info = call("/api/admin/auth/login", method="POST", token=token,
        body={"username":username, "password":password, "mfa_code":mfa})
    require(status == 200 and isinstance(info.get("session_token"), str),
            f"{username} login blocked: {safe_error(status, info)}")
    session = info["session_token"]
    try:
        status, who = call("/api/admin/auth/whoami", token=token, session=session)
        require(status == 200 and who.get("username") == username, "Identity check failed")
        return session, who.get("roles", [])
    except Exception:
        # A successful login must not leave an orphan session if identity checks fail.
        try:
            call("/api/admin/auth/logout", method="POST", token=token, session=session)
        except Exception:
            pass
        raise

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--submit", action="store_true", help="Create disposable pending changes in isolated UAT")
    args = parser.parse_args()
    require(urlparse(API).hostname == "m3-dev-sprint2-isolated-api.onrender.com", "Wrong target")
    token = normalize_uat_token(os.getenv("M3_UAT_TOKEN"))
    status, health = call("/api/v1/health")
    require(status == 200, f"Health check failed: {status}")
    require(health.get("production_promoted") is False and health.get("sandbox_only") is True,
            "Cannot verify isolated non-production runtime")
    status, readiness = call("/api/clx016/readiness")
    require(status == 200 and readiness.get("production_traffic") == "OFF" and readiness.get("live_providers") == "OFF" and readiness.get("real_money") == "OFF",
            "Isolated runtime safety flags cannot be verified")
    maker_session = checker_session = None
    pending_own_refs = []
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
            return call("/api/masterdata/bulk/submit-atomic",
                        method="POST", token=token, session=maker_session,
                        body=payload, extra_headers={"Idempotency-Key": key})
        code, first = submit(body)
        # Track only refs tied to the two unique, newly generated UAT record keys.
        # If replay or recovery later fails, these are the only refs eligible for
        # best-effort cleanup by the independent Checker.
        if code == 200 and isinstance(first, dict):
            rows = first.get("rows")
            expected = {item["record_key"] for item in body["rows"]}
            if isinstance(rows, list) and len(rows) == 2:
                reported = {row.get("record_key") for row in rows if isinstance(row, dict)}
                refs = [row.get("change_ref") for row in rows if isinstance(row, dict)]
                if reported == expected and len(refs) == 2 and len(set(refs)) == 2 and all(
                    isinstance(ref, str) and re.fullmatch(r"MDC-[A-Za-z0-9-]{4,80}", ref)
                    for ref in refs
                ):
                    pending_own_refs.extend(refs)
        require(code == 200 and first.get("atomic") is True and first.get("pending")==2,
                f"Atomic submit failed: {safe_error(code, first)}")
        require(len(pending_own_refs) == 2, "Unverifiable disposable UAT refs; manual review required")
        code, replay = submit(body)
        require(code == 200 and replay == first, f"Replay failed: HTTP {code}")
        recovery_status, recovery = call("/api/masterdata/bulk/submit-atomic/recovery?idempotency_key="+key,
            token=token,session=maker_session)
        require(recovery_status == 200 and recovery == first, "Recovery mismatch")
        changed = json.loads(json.dumps(body))
        changed["rows"][0]["payload"]["name"] += " altered"
        code, conflict = submit(changed)
        require(code == 409, f"Conflicting replay should be 409, got {code}")
        # Verify Maker cannot decide its own change, then have the independent
        # Checker reject only disposable UAT changes (no active master records).
        for ref in tuple(pending_own_refs):
            path = "/api/masterdata/changes/" + ref + "/decision"
            denied, _ = call(path, method="POST", token=token,
                session=maker_session,
                body={"decision":"REJECT","comment":"UAT four-eyes negative test"})
            require(denied == 403, f"Maker decision should be 403, got {denied}")
            approved, result = call(path, method="POST", token=token,
                session=checker_session,
                body={"decision":"REJECT","comment":"Disposable isolated UAT cleanup"})
            require(approved == 200 and result.get("status") == "REJECTED",
                f"Checker rejection failed: HTTP {approved}")
            pending_own_refs.remove(ref)
        print("PASS: atomic submission, idempotent replay, recovery, payload conflict")
        print("PASS: Maker decision denied; independent Checker rejected both UAT changes")
        print("Disposable UAT changes rejected: 2")
        print("HOLD: Checker APPROVE and rollback-on-late-failure require separate governed tests")
        return 0
    finally:
        # Cleanup only the two disposable change refs from this run, never
        # pre-existing pending changes or active Master Data.
        if pending_own_refs:
            for ref in tuple(pending_own_refs):
                if checker_session:
                    try:
                        status, response = call("/api/masterdata/changes/" + ref + "/decision",
                            method="POST", token=token, session=checker_session,
                            body={"decision":"REJECT","comment":"UAT failure-path cleanup"})
                        if status == 200 and response.get("status") == "REJECTED":
                            pending_own_refs.remove(ref)
                    except Exception:
                        pass
            if pending_own_refs:
                print("WARNING: disposable UAT pending changes require manual review",
                      file=sys.stderr)
        for session in (maker_session, checker_session):
            if session:
                try:
                    call("/api/admin/auth/logout",method="POST",token=token,session=session)
                except Exception:
                    pass
if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, KeyError, ValueError, urllib.error.URLError) as error:
        print("FAIL:",str(error),file=sys.stderr)
        sys.exit(1)
