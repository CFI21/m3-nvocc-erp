#!/usr/bin/env python3
"""Isolated M3 B-Atomic Maker LOGIN ONLY; never submits master data.

Requires approved direct Neon URL and protected M3_MAKER_PASSWORD/M3_MAKER_MFA.
One login attempt maximum, then validates expected database effects.
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import psycopg

HOST = "ep-quiet-breeze-b43xue0h.c-6.us-east-2.aws.neon.tech"
PINNED_SHA = "79968230bd5601142d9239c739853b64ed9d2f82"
MAKER = "md.maker"
MD_TABLES = ("md_change_requests", "md_audit", "md_bulk_idempotency")
AUTH_TABLES = ("iam_sessions", "iam_login_audit", "iam_users")
OTHER_TABLES = ("iam_user_roles", "iam_roles", "iam_permissions", "iam_role_permissions")


def assert_target():
    raw = os.environ.get("DATABASE_URL", "")
    u = urlsplit(raw)
    if not (u.scheme in ("postgres", "postgresql")
            and u.hostname == HOST
            and os.environ.get("M3_UAT_DB_HOST") == HOST
            and u.path == "/neondb"
            and parse_qs(u.query).get("sslmode") == ["require"]
            and "-pooler" not in (u.hostname or "")
            and "pgbouncer" not in u.query.lower()
            and u.username and u.password):
        raise RuntimeError("DATABASE_TARGET_GUARD_FAILED")
    if os.getenv("M3_UAT_PRESERVE_DATA") != "1" or os.getenv("M3_PRODUCTION_TRAFFIC") != "OFF":
        raise RuntimeError("M3_UAT_ISOLATION_FLAGS_INVALID")
    if not os.getenv("M3_MAKER_PASSWORD") or not os.getenv("M3_MAKER_MFA"):
        raise RuntimeError("MAKER_CREDENTIAL_SECRETS_MISSING")
    print("TARGET_AND_CREDENTIAL_PRESENCE_PASS", flush=True)


def assert_revision(repo):
    actual = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    if actual != PINNED_SHA:
        raise RuntimeError("PINNED_SHA_MISMATCH")
    print("PINNED_SHA_PASS", flush=True)


def capture():
    output = {}
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=12) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            for table in MD_TABLES + AUTH_TABLES + OTHER_TABLES:
                cur.execute(
                    f'SELECT count(*), COALESCE(md5(string_agg(md5(row_to_json(x)::text),'
                    f" '' ORDER BY md5(row_to_json(x)::text))), md5('')) "
                    f'FROM (SELECT * FROM public."{table}") x'
                )
                output[table] = cur.fetchone()
            cur.execute("SELECT id,username,status,mfa_required,failed_attempts,"
                        "last_login_at,locked_until FROM iam_users WHERE username=%s", (MAKER,))
            maker = cur.fetchone()
            if not maker or maker[2] != "ACTIVE" or not maker[3] or maker[4] != 0 or maker[6] is not None:
                raise RuntimeError("MAKER_ACCOUNT_PRECONDITION_FAILED")
            output["maker"] = maker
        conn.rollback()
    return output


def http_json(path, method="GET", payload=None, timeout=6):
    body = json.dumps(payload).encode() if payload is not None else None
    req = Request("http://127.0.0.1:18081" + path, data=body, method=method,
                  headers={"Content-Type": "application/json"} if body else {})
    with urlopen(req, timeout=timeout) as result:
        if result.status != 200:
            raise RuntimeError("API_HTTP_NON_200")
        return json.load(result)


def main():
    assert_target()
    repo = Path("m3-pinned").resolve()
    assert_revision(repo)
    pre = capture()
    print("PRE_STARTUP_SNAPSHOT_CAPTURED", flush=True)
    env = os.environ.copy()
    env.pop('M3_MAKER_PASSWORD', None)
    env.pop('M3_MAKER_MFA', None)
    env["PYTHONPATH"] = str(repo)
    env["PORT"] = "18081"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", "18081"],
        cwd=str(repo), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        healthy = False
        for _ in range(35):
            if proc.poll() is not None:
                raise RuntimeError("APP_EXITED_DURING_STARTUP")
            try:
                health = http_json("/api/v1/health", timeout=2)
                healthy = (health.get("project") == "M3 NVOCC ERP" and
                           health.get("database") == "postgres" and
                           health.get("sandbox_only") is True and
                           health.get("production_promoted") is False)
                if healthy:
                    break
            except Exception:
                time.sleep(1)
        if not healthy:
            raise RuntimeError("APPLICATION_NOT_HEALTHY")
        startup = capture()
        if any(pre[t] != startup[t] for t in MD_TABLES + AUTH_TABLES + OTHER_TABLES):
            raise RuntimeError("STARTUP_STATE_CHANGE_BLOCKED")
        print("ZERO_WRITE_STARTUP_PASS", flush=True)
        # Only one attempt; no retry or diagnostic password probing.
        try:
            result = http_json("/api/admin/auth/login", method="POST", timeout=15,
                               payload={"username": MAKER,
                                        "password": os.environ["M3_MAKER_PASSWORD"],
                                        "mfa_code": os.environ["M3_MAKER_MFA"]})
        except HTTPError as err:
            raise RuntimeError("MAKER_LOGIN_FAILED_HTTP_" + str(err.code)) from None
        if not (result.get("username") == MAKER and result.get("mfa_verified") is True
                and isinstance(result.get("session_token"), str)
                and len(result["session_token"]) >= 24):
            raise RuntimeError("MAKER_LOGIN_RESPONSE_INVALID")
        # Never print, save or forward the session token.
        result["session_token"] = None
        post = capture()
        if any(startup[t] != post[t] for t in MD_TABLES + OTHER_TABLES):
            raise RuntimeError("NON_AUTH_STATE_CHANGED")
        if post["iam_sessions"][0] != startup["iam_sessions"][0] + 1:
            raise RuntimeError("SESSION_COUNT_DELTA_INVALID")
        if post["iam_login_audit"][0] != startup["iam_login_audit"][0] + 1:
            raise RuntimeError("LOGIN_AUDIT_COUNT_DELTA_INVALID")
        if post["iam_users"][0] != startup["iam_users"][0]:
            raise RuntimeError("IAM_USER_COUNT_CHANGED")
        before_user, after_user = startup["maker"], post["maker"]
        if not (before_user[:5] == after_user[:5]
                and after_user[5] is not None
                and after_user[5] != before_user[5]
                and before_user[6] == after_user[6]):
            raise RuntimeError("MAKER_LAST_LOGIN_DELTA_INVALID")
        print("MAKER_LOGIN_PASS", flush=True)
        print("AUTH_EFFECTS_PASS sessions_plus_1 audit_plus_1 user_login_updated", flush=True)
        print("NON_AUTH_DATA_UNCHANGED_PASS", flush=True)
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("MAKER_AUTH_GATE_FAIL " + str(e).split(":")[0], file=sys.stderr)
        sys.exit(2)
