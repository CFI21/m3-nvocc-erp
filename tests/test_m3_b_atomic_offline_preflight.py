"""Offline regression coverage for the M3 B-Atomic evidence checker.

No network access, database queries, authentication or infrastructure changes.
"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "m3_b_atomic_offline_preflight.py"
spec = importlib.util.spec_from_file_location("m3_b_atomic_preflight", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_sha_exact_match(monkeypatch, tmp_path):
    monkeypatch.setattr(module.shutil, "which", lambda x: "/usr/bin/git")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=module.EXPECTED_SHA + "\n"))
    assert module.check_git(tmp_path)[0] == "PASS"


def test_sha_mismatch_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(module.shutil, "which", lambda x: "/usr/bin/git")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="0" * 40))
    assert module.check_git(tmp_path)[0] == "FAIL"


def test_git_missing_is_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(module.shutil, "which", lambda x: None)
    assert module.check_git(tmp_path)[0] == "BLOCKED"


@pytest.mark.parametrize("url,guard,expected", [
    ("postgresql://user:pass@isolated.example.org/neondb", "isolated.example.org", "PASS"),
    ("postgres://user:pass@isolated.example.org/neondb", "isolated.example.org", "PASS"),
    ("postgresql://user:pass@other.example.org/neondb", "isolated.example.org", "FAIL"),
    ("https://isolated.example.org/neondb", "isolated.example.org", "FAIL"),
    ("postgresql:///neondb", "isolated.example.org", "FAIL"),
    ("postgresql://x:123@isolated.example.org/db", "evil/path", "FAIL"),
])
def test_exact_database_host_guard(monkeypatch, url, guard, expected):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("M3_UAT_DB_HOST", guard)
    result, evidence = module.check_pinning()
    assert result == expected
    assert "pass" not in evidence
    assert "123" not in evidence


def test_missing_host_configuration_blocks(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("M3_UAT_DB_HOST", raising=False)
    assert module.check_pinning()[0] == "BLOCKED"


def test_maker_secret_never_considered_verified(monkeypatch):
    monkeypatch.setenv("M3_MAKER_SECRET_PROVIDER", "vault")
    assert module.check_maker()[0] == "BLOCKED"


def test_cost_and_network_exclusion_never_auto_pass():
    assert module.check_cost()[0] == "BLOCKED"
    assert module.check_exclusion()[0] == "BLOCKED"


def test_report_never_grants_go(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "check_git", lambda _: ("PASS", "SHA matches"))
    monkeypatch.setattr(module, "check_runtime", lambda: ("PASS", "deps present"))
    monkeypatch.setattr(module, "check_maker", lambda: ("BLOCKED", "not attested"))
    monkeypatch.setattr(module, "check_cost", lambda: ("BLOCKED", "not verified"))
    monkeypatch.setattr(module, "check_pinning", lambda: ("PASS", "guard matched"))
    monkeypatch.setattr(module, "check_exclusion", lambda: ("BLOCKED", "not verified"))
    report = module.build_report(tmp_path, "")
    assert report["decision"] == "NO-GO"
    assert report["owner"] == "UNASSIGNED"
    assert len(report["checks"]) == 6
    assert all(c["result"] in module.RESULTS for c in report["checks"])
    encoded = json.dumps(report)
    assert "DATABASE_URL" not in encoded


def test_python_and_dependency_version_checks(monkeypatch):
    monkeypatch.setattr(module.metadata, "version", lambda name: "1.0.0")
    result, _ = module.check_runtime()
    assert result == "PASS" if module.sys.version_info >= (3, 11) else result == "FAIL"
