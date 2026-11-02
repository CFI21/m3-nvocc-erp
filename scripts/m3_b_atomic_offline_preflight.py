#!/usr/bin/env python3
"""Offline, non-mutating M3 B-Atomic operator preflight.

This checker NEVER connects to a database, logs in, launches an app, creates
a Neon branch, or authorizes the Phase B test. Output contains no secrets.
Use only on an operator-controlled workstation. PASS here is *not* GO.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

EXPECTED_SHA = "79968230bd5601142d9239c739853b64ed9d2f82"
RESULTS = {"PASS", "FAIL", "BLOCKED"}


def check_git(root: Path):
    if not shutil.which("git"):
        return "BLOCKED", "git executable missing"
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=8, check=True
        )
        actual = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "BLOCKED", "git revision unavailable"
    return ("PASS" if actual == EXPECTED_SHA else "FAIL",
            "SHA matches approved baseline" if actual == EXPECTED_SHA
            else "SHA mismatch: expected approved baseline")


def check_runtime():
    try:
        versions = {name: metadata.version(name)
                    for name in ("fastapi", "uvicorn", "psycopg")}
    except metadata.PackageNotFoundError as exc:
        return "BLOCKED", "Missing dependency: " + str(exc)
    if sys.version_info < (3, 11):
        return "FAIL", "Python version below 3.11"
    return "PASS", "Python " + sys.version.split()[0] + "; " + "; ".join(
        name + " " + version for name, version in versions.items())


def check_maker():
    # Presence is evidence of configuration only, never successful authentication.
    method = os.environ.get("M3_MAKER_SECRET_PROVIDER", "").strip().lower()
    if method not in {"vault", "github-environment", "operator-keystore"}:
        return "BLOCKED", "Operator must attest approved secret provider (no credential values)"
    return "BLOCKED", "Secret provider configured; authorized Maker access requires operator attestation"


def check_cost():
    # Never claim cost-free capacity from environment variables or guesses.
    return "BLOCKED", "Neon Free capacity and $0 headroom require provider evidence"


def check_pinning():
    url = os.environ.get("DATABASE_URL", "")
    guard = os.environ.get("M3_UAT_DB_HOST", "")
    if not url or not guard:
        return "BLOCKED", "DATABASE_URL or M3_UAT_DB_HOST not configured"
    try:
        parsed = urlsplit(url)
        actual = parsed.hostname
    except ValueError:
        return "FAIL", "Malformed database URL"
    if parsed.scheme not in {"postgresql", "postgres"} or not actual:
        return "FAIL", "Unexpected database protocol/host"
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", guard):
        return "FAIL", "Invalid host guard"
    return ("PASS", "Exact host guard matches; host omitted") if actual.lower() == guard.lower() else (
        "FAIL", "Database host does not equal configured isolation guard")


def check_exclusion():
    # A DB hostname check cannot prove production is unreachable.
    return "BLOCKED", "Production/ANCLINE network exclusion needs independent verification"


def build_report(root: Path, owner: str):
    checks = (
        ("1", "Approved source SHA", check_git(root)),
        ("2", "Local runtime", check_runtime()),
        ("3", "Maker credential access", check_maker()),
        ("4", "Neon $0 Free capacity", check_cost()),
        ("5", "Database exact host pinning", check_pinning()),
        ("6", "Production and ANCLINE exclusion", check_exclusion()),
    )
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    return {
        "report": "M3-B-ATOMIC-EXEC-001 operator offline preflight",
        "timestamp_utc": timestamp,
        "owner": owner or "UNASSIGNED",
        "decision": "NO-GO",
        "note": "Machine checks are partial evidence; operator and independent review required.",
        "checks": [
            {"id": cid, "check": label, "result": result, "evidence": evidence,
             "owner": owner or "UNASSIGNED", "timestamp_utc": timestamp}
            for cid, label, (result, evidence) in checks
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--owner", default="")
    parser.add_argument("--output", type=Path, help="Optional local sanitized JSON report")
    args = parser.parse_args()
    report = build_report(args.repo, args.owner)
    body = json.dumps(report, indent=2)
    print(body)
    if args.output:
        args.output.write_text(body + "\n", encoding="utf-8")
    return 0 if all(c["result"] == "PASS" for c in report["checks"]) else 2


if __name__ == "__main__":
    sys.exit(main())
