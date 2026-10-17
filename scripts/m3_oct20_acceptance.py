#!/usr/bin/env python3
"""M3 Oct 20 fail-closed release acceptance evidence validator (offline, stdlib)."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

MODULES = {
    "Agent Master": ("m3-b01-b02-focused.xml", "agent_master_uat.json"),
    "IAM": ("m3-security-auth.xml", "iam_security.json"),
    "Countries/Port Auth": ("m3-cross-agent-integration.xml", "cross_agent_security.json"),
    "Booking Workflow": ("m3-b01-b02-focused.xml", "booking_job_bl_e2e.json"),
    "Equipment/Depot": ("m3-full-regression.xml", "equipment_depot_uat.json"),
    "Finance": ("m3-full-regression.xml", "finance_reconciliation.json"),
    "196-screen cross-module": ("m3-full-regression.xml", "screens_196_uat.json"),
}
REQUIRED = ("preview_deploys.json",)


def _sha():
    env = os.environ.get("GITHUB_SHA", "")
    if len(env) == 40:
        return env.lower()
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip().lower()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _junit(path):
    if not path.is_file():
        return {"status": "BLOCKED", "reason": "JUNIT_MISSING"}
    try:
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        if not suites:
            return {"status": "BLOCKED", "reason": "JUNIT_EMPTY"}
        # Avoid counting children twice if nested test suites.
        leaf = [s for s in suites if not any(c.tag == "testsuite" for c in s)]
        counts = {k: sum(int(s.get(k, "0")) for s in leaf)
                  for k in ("tests", "failures", "errors", "skipped")}
        good = (counts["tests"] > 0 and counts["failures"] == 0
                and counts["errors"] == 0 and counts["skipped"] == 0)
        return {"status": "PASS" if good else "BLOCKED", "counts": counts,
                "reason": "" if good else "JUNIT_NONPASS"}
    except (ET.ParseError, ValueError, OSError):
        return {"status": "BLOCKED", "reason": "JUNIT_UNREADABLE"}


def _artifact(path, expected_sha):
    if not path.is_file():
        return {"status": "BLOCKED", "reason": "EVIDENCE_MISSING"}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("Evidence is not an object")
        if obj.get("commit_sha", "").lower() != expected_sha:
            return {"status": "BLOCKED", "reason": "SHA_MISMATCH"}
        if obj.get("status") != "PASS":
            return {"status": "BLOCKED", "reason": "EVIDENCE_NOT_PASS"}
        if not isinstance(obj.get("artifacts"), list) or not obj["artifacts"]:
            return {"status": "BLOCKED", "reason": "PROOF_LINKS_MISSING"}
        if not all(isinstance(link, str) and link.strip() for link in obj["artifacts"]):
            return {"status": "BLOCKED", "reason": "PROOF_LINKS_INVALID"}
        return {"status": "PASS", "artifacts": obj["artifacts"]}
    except (ValueError, OSError, AttributeError):
        return {"status": "BLOCKED", "reason": "EVIDENCE_UNREADABLE"}


def evaluate(root, sha):
    report = {"commit_sha": sha, "modules": {}, "release": "BLOCKED"}
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        report["reason"] = "INVALID_OR_UNKNOWN_COMMIT_SHA"
        return report
    for name, (junit, evidence) in MODULES.items():
        ci = _junit(root / junit)
        proof = _artifact(root / evidence, sha)
        status = "PASS" if ci["status"] == proof["status"] == "PASS" else "BLOCKED"
        report["modules"][name] = {"status": status, "ci": ci,
                                    "evidence": proof}
    report["preview"] = _artifact(root / "preview_deploys.json", sha)
    report["release"] = ("PASS" if all(x["status"] == "PASS"
                          for x in report["modules"].values())
                          and report["preview"]["status"] == "PASS" else "BLOCKED")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-dir", type=Path, default=Path("acceptance-evidence"))
    ap.add_argument("--commit-sha", default=_sha())
    ap.add_argument("--output", type=Path, default=Path("m3-oct20-acceptance.json"))
    args = ap.parse_args()
    result = evaluate(args.evidence_dir, args.commit_sha.lower())
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"release": result["release"], "commit_sha": result["commit_sha"],
                      "modules": {k: v["status"] for k,v in result["modules"].items()}}, indent=2))
    return 0 if result["release"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
