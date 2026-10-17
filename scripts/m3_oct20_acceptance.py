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
    if report["preview"]["status"] == "PASS":
        try:
            deploy = json.loads((root / "preview_deploys.json").read_text(encoding="utf-8"))
            web = deploy.get("web", {})
            api = deploy.get("api", {})
            for target in (web, api):
                if not isinstance(target, dict) or target.get("commit_sha", "").lower() != sha:
                    raise ValueError("DEPLOY_SHA_MISMATCH")
                if not str(target.get("deployment_id", "")).strip():
                    raise ValueError("DEPLOY_ID_MISSING")
            if web["deployment_id"] == api["deployment_id"]:
                raise ValueError("DEPLOY_IDENTITIES_NOT_DISTINCT")
        except (ValueError, OSError, AttributeError, KeyError) as exc:
            report["preview"] = {"status": "BLOCKED", "reason": str(exc)}
    # Release promotion requires explicit authenticated E2E, cross-agent,
    # finance and full UI evidence: an arbitrary PASS document is not enough.
    scopes = {
        "Countries/Port Auth": ("SEC-001", "SEC-002", "SEC-003", "SEC-004",
                                "SEC-005", "SEC-006", "SEC-007", "SEC-008",
                                "SEC-009", "SEC-010", "SEC-011", "SEC-012"),
        "Booking Workflow": ("E2E-001", "E2E-002", "E2E-003", "E2E-004"),
        "Agent Master": ("UAT-001", "UAT-002"),
        "IAM": ("UAT-003",),
        "Equipment/Depot": ("UAT-007",),
        "Finance": ("UAT-008",),
        "196-screen cross-module": ("UAT-010",),
    }
    for name, required in scopes.items():
        if report["modules"][name]["status"] != "PASS":
            continue
        filename = MODULES[name][1]
        try:
            document = json.loads((root / filename).read_text(encoding="utf-8"))
            scenarios = document.get("scenarios", {})
            if (not isinstance(scenarios, dict)
                    or any(not isinstance(scenarios.get(sid), dict)
                           or scenarios[sid].get("status") != "PASS"
                           or not scenarios[sid].get("artifacts") for sid in required)):
                raise ValueError("MANDATORY_SCENARIOS_MISSING")
            if name == "196-screen cross-module":
                screens = document.get("screens", {})
                if (not isinstance(screens, dict) or len(screens) != 196
                        or any(v.get("status") != "PASS" or not v.get("artifacts")
                               for v in screens.values() if isinstance(v, dict))
                        or any(not isinstance(v, dict) for v in screens.values())):
                    raise ValueError("SCREENS_196_NOT_VERIFIED")
        except (OSError, ValueError, AttributeError, KeyError) as error:
            report["modules"][name]["status"] = "BLOCKED"
            report["modules"][name]["reason"] = str(error)
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
