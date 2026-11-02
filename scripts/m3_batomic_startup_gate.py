#!/usr/bin/env python3
"""B-Atomic disposable Neon startup-only verification. No login or submission.

This script requires an exact direct-host match and a pinned application checkout.
It snapshots six tables, starts the application, verifies startup readiness, and
snapshots again. FAIL CLOSED on errors or any persisted row-state difference.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

EXPECTED_HOST = "ep-quiet-breeze-b43xue0h.c-6.us-east-2.aws.neon.tech"
EXPECTED_SHA = "79968230bd5601142d9239c739853b64ed9d2f82"
TABLES = (
    "md_change_requests", "md_audit", "md_bulk_idempotency",
    "iam_sessions", "iam_login_audit", "iam_users",
)


def validate_target():
    url = os.environ.get("DATABASE_URL", "")
    host_guard = os.environ.get("M3_UAT_DB_HOST", "")
    parsed = urlsplit(url)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise RuntimeError("Invalid database URL scheme")
    if parsed.hostname != EXPECTED_HOST or host_guard != EXPECTED_HOST:
        raise RuntimeError("Direct Neon hostname guard failed")
    if "-pooler" in parsed.hostname or "pgbouncer" in parsed.query.lower():
        raise RuntimeError("Pooled connections prohibited")
    if "sslmode=require" not in parsed.query.split("&"):
        raise RuntimeError("TLS requirement absent")
    print("DIRECT_HOST_GUARD_PASS (credentials redacted)", flush=True)


def validate_checkout(repo):
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=10
    )
    if result.stdout.strip() != EXPECTED_SHA:
        raise RuntimeError("Pinned application SHA mismatch")
    print("PINNED_SHA_PASS", flush=True)


def snapshot():
    result = {}
    # Repeatable-read consistency within each snapshot, no explicit writes.
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=False,
                         connect_timeout=12) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            for table in TABLES:
                # Canonical row signatures, sorted; includes changes to existing rows.
                cur.execute(
                    f'SELECT COUNT(*), COALESCE(md5(string_agg(md5(row_to_json(x)::text),'
                    f" '' ORDER BY md5(row_to_json(x)::text))), md5('')) "
                    f'FROM (SELECT * FROM public."{table}") AS x'
                )
                count, digest = cur.fetchone()
                result[table] = (count, digest)
        conn.rollback()
    return result


def main():
    repo = Path(os.environ.get("M3_PINNED_REPO", "m3-pinned")).resolve()
    validate_target()
    validate_checkout(repo)
    if os.environ.get('M3_UAT_PRESERVE_DATA') != '1':
        raise RuntimeError('UAT preservation flag missing')
    if os.environ.get('M3_PRODUCTION_TRAFFIC', '').upper() != 'OFF':
        raise RuntimeError('Production traffic must be OFF')
    before = snapshot()
    print("PRE_START_SNAPSHOT_CAPTURED", flush=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    env["PORT"] = "18080"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", "18080"],
        cwd=str(repo), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        # No GET and no login; local TCP socket is enough to detect listening.
        import socket
        started = False
        for _ in range(35):
            if proc.poll() is not None:
                raise RuntimeError("Application exited before listening")
            try:
                with socket.create_connection(("127.0.0.1", 18080), timeout=0.5):
                    started = True
                    break
            except OSError:
                time.sleep(1)
        if not started:
            raise RuntimeError("Application startup timed out")
        print("APPLICATION_LISTENING", flush=True)
        # A listening socket alone does not prove FastAPI startup completed.
        from urllib.request import urlopen
        import json
        with urlopen('http://127.0.0.1:18080/api/v1/health', timeout=8) as response:
            if response.status != 200:
                raise RuntimeError('Health endpoint not ready')
            health = json.load(response)
        if health.get('project') != 'M3 NVOCC ERP' or health.get('sandbox_only') is not True or health.get('production_promoted') is not False:
            raise RuntimeError('Unsafe or unexpected application health state')
        print('APPLICATION_HEALTH_PASS', flush=True)
        after = snapshot()
        changes = [table for table in TABLES if before[table] != after[table]]
        if changes:
            print("STARTUP_STATE_CHANGE_BLOCKED tables=" + ",".join(changes),
                  flush=True)
            return 2
        print("ZERO_WRITE_STARTUP_PASS six_tables_unchanged", flush=True)
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
        raise SystemExit(main())
    except Exception as exc:
        # No URL or database exception details (could contain credentials).
        print("STARTUP_PREFLIGHT_FAIL " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(2)
