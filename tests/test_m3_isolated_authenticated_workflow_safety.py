"""Safety contract of manual authenticated UAT; uses no live credentials."""
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/m3-isolated-authenticated-masterdata-uat.yml"
RUNNER = ROOT / "scripts/m3_authenticated_masterdata_uat.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("isolated_auth_uat_contract", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_workflow_is_manual_branch_limited_and_environment_gated():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "github.ref == 'refs/heads/m3-dev-sprint-2'" in source
    assert "inputs.confirmation == 'ISOLATED_UAT_WRITE_ONLY'" in source
    assert "environment: m3-isolated-uat" in source
    assert "permissions:\n  contents: read" in source
    assert "cancel-in-progress: false" in source
    assert "persist-credentials: false" in source
    assert 'test "$GITHUB_REF" = ' in source
    for name in ("M3_UAT_TOKEN", "M3_UAT_MAKER_PASSWORD",
                 "M3_UAT_CHECKER_PASSWORD", "M3_UAT_MAKER_MFA_CODE",
                 "M3_UAT_CHECKER_MFA_CODE"):
        assert "secrets." + name in source
    assert "if: always()" in source
    assert "actions/upload-artifact@v4" in source
    assert "\n  push:" not in source
    assert "\n  pull_request:" not in source


def test_missing_confirmation_and_credentials_fail_without_network(monkeypatch):
    runner = load_runner()
    for key in ("M3_UAT_CONFIRM", "M3_UAT_TOKEN",
                "M3_UAT_MAKER_PASSWORD", "M3_UAT_CHECKER_PASSWORD",
                "M3_UAT_MAKER_MFA_CODE", "M3_UAT_CHECKER_MFA_CODE"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(runner.UATFailure, match="confirmation missing"):
        runner.authorize()
    monkeypatch.setenv("M3_UAT_CONFIRM", "ISOLATED_UAT_WRITE_ONLY")
    with pytest.raises(runner.UATFailure, match="Missing secured MAKER"):
        runner.authorize()
    monkeypatch.setenv("M3_UAT_MAKER_PASSWORD", "disposable-test-placeholder")
    monkeypatch.setenv("M3_UAT_MAKER_MFA_CODE", "not-a-real-mfa")
    with pytest.raises(runner.UATFailure, match="Missing secured CHECKER"):
        runner.authorize()


def test_sandbox_preflight_requires_traffic_lock(monkeypatch):
    runner = load_runner()
    health = {"project": "M3 NVOCC ERP", "sandbox_only": True,
              "production_promoted": False, "database": "postgres"}
    ready = {"project": "M3 NVOCC ERP", "database_health": "ok",
             "production_traffic": "OFF", "live_providers": "OFF", "real_money": "OFF"}
    monkeypatch.setattr(runner, "request", lambda path, **kw:
                        health if path == "/api/v1/health" else ready)
    assert runner.check_sandbox()
    ready["production_traffic"] = "ON"
    with pytest.raises(runner.UATFailure, match="Unsafe isolated API readiness"):
        runner.check_sandbox()


def test_redirect_handler_rejects_cross_host_redirects():
    runner = load_runner()
    with pytest.raises(runner.UATFailure, match="forbidden redirect"):
        runner.ForbidRedirect().redirect_request(
            None, None, 302, "redirect", {}, "https://untrusted.invalid/")


def test_login_identity_failure_revokes_issued_session(monkeypatch):
    runner = load_runner()
    calls = []
    def fake_request(path, *, method="GET", payload=None, token=None, status=200):
        calls.append((path, method, token))
        if path == "/api/admin/auth/login":
            return {"username": "md.maker", "mfa_verified": True,
                    "roles": ["MASTER_DATA"], "user_ref": "isolated-maker",
                    "session_token": "test-disposable-token"}
        if path == "/api/admin/auth/whoami":
            return {"username": "other-user", "roles": ["MASTER_DATA"],
                    "user_ref": "other-identity"}
        if path == "/api/admin/auth/logout":
            return {"status": "LOGGED_OUT"}
        raise AssertionError("Unrecognized request")
    monkeypatch.setattr(runner, "request", fake_request)
    with pytest.raises(runner.UATFailure, match="IAM session identity verification failed"):
        runner.login("md.maker", "unused-password", "unused-mfa", "MASTER_DATA")
    assert calls[-1] == ("/api/admin/auth/logout", "POST", "test-disposable-token")


def test_unexpected_exception_details_never_enter_evidence_or_stderr():
    runner = load_runner()
    exc = RuntimeError("PASSWORD=private-test-placeholder SESSION=secret-token")
    assert runner.safe_failure(exc) == "UNEXPECTED_RuntimeError"
    assert "private-test-placeholder" not in runner.safe_failure(exc)
    assert runner.safe_failure(runner.UATFailure("Missing secured MAKER login configuration")) == (
        "Missing secured MAKER login configuration")


def test_uat_gate_token_required_separately_from_iam_secrets(monkeypatch):
    runner = load_runner()
    monkeypatch.setenv("M3_UAT_CONFIRM", "ISOLATED_UAT_WRITE_ONLY")
    for label in ("MAKER", "CHECKER"):
        monkeypatch.setenv(f"M3_UAT_{label}_PASSWORD", "disposable-offline-placeholder")
        monkeypatch.setenv(f"M3_UAT_{label}_MFA_CODE", "disposable-offline-mfa")
    monkeypatch.delenv("M3_UAT_TOKEN", raising=False)
    with pytest.raises(runner.UATFailure, match="UAT access-token configuration"):
        runner.authorize()
    monkeypatch.setenv("M3_UAT_TOKEN", "short")
    with pytest.raises(runner.UATFailure, match="UAT access-token configuration"):
        runner.authorize()
    monkeypatch.setenv("M3_UAT_TOKEN", "a" * 32)
    assert sorted(runner.authorize()) == ["CHECKER", "MAKER"]


def test_pinned_isolated_request_forwards_distinct_uat_header(monkeypatch):
    runner = load_runner()
    test_gate = "offline-u-a-t-gate-" + "x" * 40
    monkeypatch.setenv("M3_UAT_TOKEN", test_gate)
    captured = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b'{"ok":true}'
    class Opener:
        def open(self, req, timeout):
            captured.append({"url": req.full_url,
                             "method": req.get_method(),
                             "headers": dict(req.header_items())})
            return Response()
    monkeypatch.setattr(runner, "HTTP", Opener())
    assert runner.request("/api/admin/auth/whoami", token="offline-session") == {"ok": True}
    assert len(captured) == 1
    req = captured[0]
    assert req["url"] == "https://m3-dev-sprint2-isolated-api.onrender.com/api/admin/auth/whoami"
    assert req["method"] == "GET"
    headers = {k.lower(): v for k, v in req["headers"].items()}
    assert headers["x-m3-uat-token"] == test_gate
    assert headers["x-m3-session"] == "offline-session"


def test_login_response_role_mismatch_revokes_issued_session(monkeypatch):
    runner = load_runner()
    calls = []
    def stub(path, *, method="GET", payload=None, token=None, status=200):
        calls.append((path, method, token))
        if path == "/api/admin/auth/login":
            return {"username": "md.maker", "mfa_verified": True,
                    "roles": ["VIEWER"], "user_ref": "test-maker",
                    "session_token": "issued-disposable-session"}
        if path == "/api/admin/auth/logout":
            return {"status": "LOGGED_OUT"}
        raise AssertionError("No whoami should occur with wrong role")
    monkeypatch.setattr(runner, "request", stub)
    with pytest.raises(runner.UATFailure, match="IAM login/MFA/role verification failed"):
        runner.login("md.maker", "never-a-real-password", "not-a-real-mfa", "MASTER_DATA")
    assert calls[-1] == ("/api/admin/auth/logout", "POST", "issued-disposable-session")
    assert not any(path == "/api/admin/auth/whoami" for path, _, _ in calls)


def test_login_without_issued_token_does_not_attempt_logout(monkeypatch):
    runner = load_runner()
    calls = []
    def stub(path, *, method="GET", payload=None, token=None, status=200):
        calls.append((path, method, token))
        assert path == "/api/admin/auth/login"
        return {"username": "md.maker", "mfa_verified": True,
                "roles": ["MASTER_DATA"], "user_ref": "test-maker"}
    monkeypatch.setattr(runner, "request", stub)
    with pytest.raises(runner.UATFailure, match="IAM login/MFA/role verification failed"):
        runner.login("md.maker", "never-a-real-password", "not-a-real-mfa", "MASTER_DATA")
    assert len(calls) == 1
