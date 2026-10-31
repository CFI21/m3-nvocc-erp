"""Credential-free tests of the standalone isolated runner's failure-path cleanup.

No network calls, real credentials or persistent databases are used.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

script = Path(__file__).resolve().parents[1] / "scripts" / "m3_isolated_atomic_bulk_uat.py"
spec = importlib.util.spec_from_file_location("m3_cleanup_runner", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def runner(monkeypatch):
    for name in (
        "M3_UAT_TOKEN", "M3_MAKER_PASSWORD", "M3_MAKER_MFA",
        "M3_CHECKER_PASSWORD", "M3_CHECKER_MFA"
    ):
        monkeypatch.setenv(name, "offline-fixture")
    monkeypatch.setattr(sys, "argv", ["m3_isolated_atomic_bulk_uat.py", "--submit"])
    monkeypatch.setattr(
        module, "login",
        lambda username, *_: (
            ("maker-session", ["MASTER_DATA"]) if username == "md.maker"
            else ("checker-session", ["MASTER_DATA_MANAGER"])
        ),
    )
    return module


def install_gateway(monkeypatch, runner, *, replay_error=False, checker_cleanup_failure=False):
    observed = {"submissions": 0, "decisions": [], "logouts": [], "expected": set()}
    original_first = {}

    def fake_call(path, *, method="GET", token=None, session=None, body=None, extra_headers=None):
        if path == "/api/v1/health":
            return 200, {"sandbox_only": True, "production_promoted": False}
        if path == "/api/clx016/readiness":
            return 200, {"production_traffic": "OFF", "live_providers": "OFF", "real_money": "OFF"}
        if path == "/api/masterdata/bulk/submit-atomic":
            observed["submissions"] += 1
            assert session == "maker-session"
            assert extra_headers["Idempotency-Key"].startswith("m3-uat-")
            if observed["submissions"] == 1:
                rows = []
                for idx, item in enumerate(body["rows"], start=1):
                    ref = f"MDC-UAT-CLEAN-{idx}"
                    rows.append({"record_key": item["record_key"], "change_ref": ref, "status": "PENDING", "row": idx})
                original_first.update({"atomic": True, "pending": 2, "rows": rows, "domain": "agent", "rejected": 0, "total": 2})
                observed["expected"] = {r["change_ref"] for r in rows}
                return 200, dict(original_first)
            if replay_error:
                return 502, {}
            if observed["submissions"] == 2:
                return 200, dict(original_first)
            return 409, {"detail": {"code": "BULK_IDEMPOTENCY_PAYLOAD_CONFLICT"}}
        if path.startswith("/api/masterdata/bulk/submit-atomic/recovery"):
            assert session == "maker-session"
            return 200, dict(original_first)
        if path.startswith("/api/masterdata/changes/") and path.endswith("/decision"):
            ref = path.split("/")[-2]
            observed["decisions"].append((ref, session, body["decision"]))
            if session == "maker-session":
                return 403, {"detail": {"code": "MAKER_CANNOT_APPROVE"}}
            assert session == "checker-session"
            if checker_cleanup_failure:
                return 503, {"detail": {"code": "TEMPORARY_UNAVAILABLE"}}
            return 200, {"status": "REJECTED"}
        if path == "/api/admin/auth/logout":
            observed["logouts"].append(session)
            return 200, {}
        raise AssertionError(f"Unexpected path: {path}")

    monkeypatch.setattr(runner, "call", fake_call)
    return observed


def test_replay_failure_rejects_only_new_disposable_changes(monkeypatch, runner):
    seen = install_gateway(monkeypatch, runner, replay_error=True)
    with pytest.raises(RuntimeError, match="Replay failed"):
        runner.main()
    checker_refs = {ref for ref, session, decision in seen["decisions"] if session == "checker-session"}
    assert checker_refs == seen["expected"]
    assert "pre-existing-change" not in checker_refs
    assert seen["logouts"] == ["maker-session", "checker-session"]


def test_normal_flow_rejects_each_change_once(monkeypatch, runner):
    seen = install_gateway(monkeypatch, runner)
    assert runner.main() == 0
    checker_refs = [ref for ref, session, _ in seen["decisions"] if session == "checker-session"]
    assert len(checker_refs) == 2
    assert set(checker_refs) == seen["expected"]
    assert seen["logouts"] == ["maker-session", "checker-session"]


def test_cleanup_failure_reports_manual_review(monkeypatch, runner, capsys):
    seen = install_gateway(monkeypatch, runner, replay_error=True, checker_cleanup_failure=True)
    with pytest.raises(RuntimeError, match="Replay failed"):
        runner.main()
    captured = capsys.readouterr()
    assert "manual review" in captured.err
    assert len([r for r, session, _ in seen["decisions"] if session == "checker-session"]) == 2
    assert seen["logouts"] == ["maker-session", "checker-session"]
