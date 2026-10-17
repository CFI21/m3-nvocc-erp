"""Oct 20 acceptance gate: missing, failed or cross-commit evidence must block."""
import json
import xml.etree.ElementTree as ET

from scripts.m3_oct20_acceptance import MODULES, _artifact, _junit, evaluate

SHA="f"*40


def _xml(path, tests=2, failures=0, errors=0, skipped=0):
    root=ET.Element("testsuite",tests=str(tests),failures=str(failures),
                    errors=str(errors),skipped=str(skipped))
    ET.ElementTree(root).write(path)


def test_default_empty_evidence_is_blocked(tmp_path):
    report=evaluate(tmp_path,SHA)
    assert report["release"]=="BLOCKED"
    assert len(report["modules"])==7
    assert all(v["status"]=="BLOCKED" for v in report["modules"].values())


def test_zero_test_junit_and_unexpected_skip_block(tmp_path):
    p=tmp_path/"suite.xml"
    _xml(p,tests=0)
    assert _junit(p)["status"]=="BLOCKED"
    _xml(p,tests=3,skipped=1)
    assert _junit(p)["status"]=="BLOCKED"
    _xml(p,tests=3,failures=1)
    assert _junit(p)["status"]=="BLOCKED"
    _xml(p,tests=3)
    assert _junit(p)["status"]=="PASS"


def test_module_evidence_requires_SHA_and_artifacts(tmp_path):
    p=tmp_path/"evidence.json"
    p.write_text(json.dumps({"commit_sha":"e"*40,"status":"PASS",
                             "artifacts":["actions/run/123"]}))
    assert _artifact(p,SHA)["reason"]=="SHA_MISMATCH"
    p.write_text(json.dumps({"commit_sha":SHA,"status":"PASS","artifacts":[]}))
    assert _artifact(p,SHA)["reason"]=="PROOF_LINKS_MISSING"
    p.write_text(json.dumps({"commit_sha":SHA,"status":"PASS",
                             "artifacts":["actions/run/123"]}))
    assert _artifact(p,SHA)["status"]=="PASS"


def test_all_seven_modules_and_preview_required_for_release_pass(tmp_path):
    for junit,filename in MODULES.values():
        _xml(tmp_path/junit)
        (tmp_path/filename).write_text(json.dumps({
            "commit_sha":SHA,"status":"PASS",
            "artifacts":["https://example.invalid/test-evidence"]}))
    report=evaluate(tmp_path,SHA)
    assert all(v["status"]=="PASS" for v in report["modules"].values())
    assert report["release"]=="BLOCKED"
    (tmp_path/"preview_deploys.json").write_text(json.dumps({
        "commit_sha":SHA,"status":"PASS",
        "artifacts":["https://example.invalid/deploy-evidence"]}))
    assert evaluate(tmp_path,SHA)["release"]=="PASS"


def test_unknown_sha_is_blocked(tmp_path):
    assert evaluate(tmp_path,"faac7b39")["reason"]=="INVALID_OR_UNKNOWN_COMMIT_SHA"
