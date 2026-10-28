#!/usr/bin/env python3
"""Authenticated LIVE master-data UAT for the isolated M3 API only.

Run from an authorized environment with M3_UAT_CONFIRM=ISOLATED_UAT_WRITE_ONLY
and M3_UAT_MAKER_PASSWORD / M3_UAT_CHECKER_PASSWORD plus each account's
M3_UAT_MAKER_MFA_CODE / M3_UAT_CHECKER_MFA_CODE.

This intentionally creates governed UAT change requests and a record that is
deactivated at the end. Never use production credentials or production URLs.
Secrets and session tokens are not printed or written to evidence.
"""
import datetime as dt
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
import uuid

API = "https://m3-dev-sprint2-isolated-api.onrender.com"
MAKER_NAME = "md.maker"
CHECKER_NAME = "md.checker"
EVIDENCE_PATH = Path(os.getenv("M3_UAT_EVIDENCE_PATH", "m3-live-uat-evidence.json"))


class UATFailure(RuntimeError):
    pass


class ForbidRedirect(urllib.request.HTTPRedirectHandler):
    """Credentials must never be forwarded to redirected destinations."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UATFailure("Isolated API returned a forbidden redirect")


HTTP = urllib.request.build_opener(ForbidRedirect)


def require(condition, message):
    if not condition:
        raise UATFailure(message)


def request(path, *, method="GET", payload=None, token=None, status=200):
    require(path.startswith("/api/") and "//" not in path, "Unapproved API path")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-M3-Session"] = token
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with HTTP.open(req, timeout=25) as response:
            actual, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        actual, raw = error.code, error.read()
    except urllib.error.URLError:
        raise UATFailure("Isolated API unreachable") from None
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise UATFailure("API returned non-JSON response") from None
    require(actual == status, f"{method} {path.split('?')[0]} HTTP {actual}, expected {status}")
    return body


def error_code(body):
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail.get("code") if isinstance(detail, dict) else None


def assert_denied(path, *, method, payload, token, status, code):
    body = request(path, method=method, payload=payload, token=token, status=status)
    require(error_code(body) == code, f"Expected denial {code}")


def authorize():
    require(os.getenv("M3_UAT_CONFIRM") == "ISOLATED_UAT_WRITE_ONLY",
            "Explicit isolated-data-mutation confirmation missing")
    credentials = {}
    for label in ("MAKER", "CHECKER"):
        password = os.getenv(f"M3_UAT_{label}_PASSWORD", "")
        mfa = os.getenv(f"M3_UAT_{label}_MFA_CODE", "")
        require(bool(password and mfa), f"Missing secured {label} login configuration")
        credentials[label] = (password, mfa)
    return credentials


def check_sandbox():
    health = request("/api/v1/health")
    readiness = request("/api/clx016/readiness")
    require(health.get("project") == "M3 NVOCC ERP"
            and health.get("sandbox_only") is True
            and health.get("production_promoted") is False
            and health.get("database") == "postgres", "Unexpected health target")
    require(readiness.get("project") == "M3 NVOCC ERP"
            and readiness.get("database_health") == "ok"
            and readiness.get("production_traffic") == "OFF"
            and readiness.get("live_providers") == "OFF"
            and readiness.get("real_money") == "OFF", "Unsafe isolated API readiness")
    return True


def login(username, password, mfa_code, required_role):
    response = request("/api/admin/auth/login", method="POST",
                       payload={"username": username, "password": password,
                                "mfa_code": mfa_code})
    require(response.get("username") == username
            and response.get("mfa_verified") is True
            and required_role in response.get("roles", [])
            and bool(response.get("session_token")), "IAM login/MFA/role verification failed")
    token = response["session_token"]
    who = request("/api/admin/auth/whoami", token=token)
    require(who.get("username") == username
            and who.get("user_ref") == response.get("user_ref")
            and required_role in who.get("roles", []), "IAM session identity verification failed")
    return token, response["user_ref"]


def change(token, record_key, operation, name):
    body = request("/api/masterdata/changes", method="POST", token=token,
                   payload={"domain": "customer", "record_key": record_key,
                            "operation": operation, "payload": {"name": name},
                            "reason": "Isolated authenticated UAT"})
    require(body.get("status") == "PENDING" and body.get("change_ref"),
            "Governed master-data request was not pending")
    return body["change_ref"]


def decide(token, ref, decision, expected_status, expected_version=None):
    body = request(f"/api/masterdata/changes/{ref}/decision", method="POST",
                   token=token, payload={"decision": decision,
                                         "comment": "Isolated authenticated UAT"})
    require(body.get("status") == expected_status, "Incorrect checker decision")
    if expected_version is not None:
        require(body.get("version") == expected_version, "Unexpected approved version")


def evidence_check(token, key, versions, approved_refs):
    body = request(f"/api/masterdata/versions/customer/{key}", token=token)
    require([v.get("version") for v in body] == list(range(versions, 0, -1)),
            "Version history mismatch")
    require(all(v.get("change_ref") in approved_refs for v in body),
            "Unapproved change in version history")
    for ref in approved_refs:
        trail = request(f"/api/masterdata/audit?change_ref={ref}", token=token)
        actions = {row.get("action") for row in trail}
        require({"CHANGE_REQUEST_CREATE", "CHANGE_APPROVE_APPLY"} <= actions,
                "Missing governed approval audit")
    return True


def bulk(token, key1, key2):
    data = {"domain": "customer", "rows": [
        {"record_key": key1, "operation": "CREATE",
         "payload": {"name": f"Isolated UAT {key1}"}, "reason": "Isolated authenticated UAT"},
        {"record_key": key2, "operation": "CREATE",
         "payload": {"name": f"Isolated UAT {key2}"}, "reason": "Isolated authenticated UAT"},
    ]}
    p = request("/api/masterdata/bulk/preview", method="POST", token=token, payload=data)
    require(p.get("valid") == 2 and p.get("rejected") == 0, "Bulk preview failed")
    s = request("/api/masterdata/bulk/submit", method="POST", token=token, payload=data)
    require(s.get("pending") == 2 and s.get("rejected") == 0, "Bulk submit failed")
    refs = [r.get("change_ref") for r in s.get("rows", [])]
    require(len(refs) == 2 and all(refs) and refs[0] != refs[1],
            "Unexpected bulk change references")
    return refs


def main():
    # All safety checks are required before any authenticated mutation.
    credentials = authorize()
    check_sandbox()
    maker_token = checker_token = None
    cleanup_ref = None
    live_record = None
    result = {"executed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "target": API, "status": "FAIL", "checks": [],
              "test_record_keys": [], "change_references": []}
    checks = result["checks"]
    try:
        maker_token, maker_ref = login(MAKER_NAME, *credentials["MAKER"], "MASTER_DATA")
        checker_token, checker_ref = login(CHECKER_NAME, *credentials["CHECKER"],
                                          "MASTER_DATA_MANAGER")
        require(maker_ref != checker_ref, "Maker/checker identities are not independent")
        checks.append("IAM distinct actors, live sessions, sandbox MFA and role assignments")
        suffix = uuid.uuid4().hex[:10].upper()
        first, second, withdrawn, self_key = (
            f"UAT-AUTH-{suffix}-A", f"UAT-AUTH-{suffix}-B",
            f"UAT-AUTH-{suffix}-C", f"UAT-AUTH-{suffix}-D")
        result["test_record_keys"] = [first, second, withdrawn, self_key]
        pending_first, pending_second = bulk(maker_token, first, second)
        checks.append("Bulk preview and governed pending submission")
        assert_denied(f"/api/masterdata/changes/{pending_first}/decision",
                      method="POST", payload={"decision": "APPROVE"},
                      token=maker_token, status=403, code="CHECKER_PERMISSION_DENIED")
        checks.append("Maker permission denied for independent checker action")
        decide(checker_token, pending_first, "APPROVE", "APPROVED", 1)
        live_record = first
        decide(checker_token, pending_second, "REJECT", "REJECTED")
        checks.append("Independent checker approved first and rejected second")
        evidence_check(checker_token, first, 1, [pending_first])
        update_ref = change(maker_token, first, "UPDATE", f"Isolated UAT updated {suffix}")
        decide(checker_token, update_ref, "APPROVE", "APPROVED", 2)
        evidence_check(checker_token, first, 2, [pending_first, update_ref])
        checks.append("Version 1/2 history and exact change-scoped approval audits")
        withdraw_ref = change(maker_token, withdrawn, "CREATE",
                              f"Isolated UAT withdrawn {suffix}")
        withdrawn_response = request(
            f"/api/masterdata/changes/{withdraw_ref}/withdraw", method="POST",
            token=maker_token, payload={"reason": "Isolated UAT withdrawal"})
        require(withdrawn_response.get("status") == "WITHDRAWN", "Withdrawal failed")
        assert_denied(f"/api/masterdata/changes/{withdraw_ref}/decision",
                      method="POST", payload={"decision": "APPROVE"},
                      token=checker_token, status=409, code="CHANGE_ALREADY_DECIDED")
        checks.append("Maker withdrawal cannot later be checker-approved")
        self_ref = change(checker_token, self_key, "CREATE",
                          f"Isolated UAT self-denied {suffix}")
        assert_denied(f"/api/masterdata/changes/{self_ref}/decision",
                      method="POST", payload={"decision": "APPROVE"},
                      token=checker_token, status=409, code="FOUR_EYES_VIOLATION")
        withdrawn_self = request(
            f"/api/masterdata/changes/{self_ref}/withdraw", method="POST",
            token=checker_token, payload={"reason": "Withdraw denied self-approval UAT"})
        require(withdrawn_self.get("status") == "WITHDRAWN",
                "Self-approval test cleanup failed")
        checks.append("Four-eyes violation blocked and self-created request withdrawn")
        duplicate = {"domain": "customer", "rows": [
            {"record_key": first.lower(), "operation": "CREATE",
             "payload": {"name": f"Duplicate test {suffix}"},
             "reason": "Isolated authenticated UAT"}]}
        collision = request("/api/masterdata/bulk/preview",
                            method="POST", payload=duplicate, token=maker_token)
        require(collision.get("valid") == 0
                and "RECORD_ALREADY_EXISTS" in collision["rows"][0]["errors"],
                "Case-variant duplicate not rejected")
        checks.append("Case-insensitive duplicate blocked")
        cleanup_ref = change(maker_token, first, "DEACTIVATE", "")
        decide(checker_token, cleanup_ref, "APPROVE", "APPROVED", 3)
        evidence_check(checker_token, first, 3, [pending_first, update_ref, cleanup_ref])
        live_record = None
        checks.append("Governed deactivation completed; no active test customer retained")
        result["change_references"] = [
            pending_first, pending_second, update_ref, withdraw_ref, self_ref, cleanup_ref]
        result["status"] = "PASS"
    except Exception as exc:
        result["failure"] = str(exc)
        raise
    finally:
        # Keep governed records visible for review on failure; no direct DB edits.
        if live_record:
            result["active_test_record_needing_review"] = live_record
        for label, token in (("maker", maker_token), ("checker", checker_token)):
            if token:
                try:
                    request("/api/admin/auth/logout", method="POST", token=token)
                except Exception:
                    result.setdefault("logout_errors", []).append(label)
        if result.get("logout_errors"):
            result["status"] = "FAIL"
        # Evidence contains only UAT identifiers and verification outcomes,
        # never login credentials, bearer tokens or MFA codes.
        EVIDENCE_PATH.write_text(json.dumps(result, indent=2) + "\\n", encoding="utf-8")
    require(result["status"] == "PASS", "Live acceptance or session revocation failed")
    return result


if __name__ == "__main__":
    try:
        result = main()
    except Exception as exc:
        # Do not print request payloads, credential values or bearer tokens.
        print("M3_ISOLATED_AUTHENTICATED_UAT_FAIL: " + str(exc), file=sys.stderr)
        sys.exit(1)
    EVIDENCE_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("M3_ISOLATED_AUTHENTICATED_UAT_PASS")
    print("Evidence written to " + str(EVIDENCE_PATH))
