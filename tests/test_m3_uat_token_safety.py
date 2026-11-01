"""Unit tests for the standalone isolated M3 UAT runner; no network or credentials."""
import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / "scripts" / "m3_isolated_atomic_bulk_uat.py"
spec = importlib.util.spec_from_file_location("m3_uat", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize(("raw", "expected"), [
    ("  example-token  ", "example-token"),
    ("\r\nabc+def=\t", "abc+def="),
    ("ab\r\ncd==", "abcd=="),
    ("abc+def=", "abc+def="),
])
def test_token_normalizes_copy_paste(raw, expected):
    assert module.normalize_uat_token(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "bad\x00value", "bad\x7fvalue"])
def test_token_rejects_invalid_characters(raw):
    with pytest.raises(RuntimeError):
        module.normalize_uat_token(raw)


def test_auth_error_does_not_echo_untrusted_response():
    raw = "sensitive-value-must-not-be-printed"
    result = module.safe_error(400, {"detail": {"code": raw}})
    assert raw not in result
    assert "HTTP 400" in result


def test_auth_error_allows_only_safe_machine_code():
    result = module.safe_error(503, {"detail": {"code": "PRODUCTION_TRAFFIC_LOCKED"}})
    assert "PRODUCTION_TRAFFIC_LOCKED" in result


def test_failed_whoami_logs_out_existing_session(monkeypatch):
    seen = []
    def fake_call(path, *, method="GET", token=None, session=None, body=None):
        seen.append((path, method))
        if path.endswith("/login"):
            return 200, {"session_token": "dummy-session"}
        if path.endswith("/whoami"):
            return 403, {}
        if path.endswith("/logout"):
            assert session == "dummy-session"
            return 200, {}
        raise AssertionError("unexpected path")

    monkeypatch.setattr(module, "call", fake_call)
    with pytest.raises(RuntimeError, match="Identity check failed"):
        module.login("md.maker", "fake-password", "fake-mfa", "fake-token")
    assert ("/api/admin/auth/logout", "POST") in seen


def test_failed_login_never_attempts_whoami(monkeypatch):
    calls = []
    def fake_call(path, **kwargs):
        calls.append(path)
        return 400, {"detail": {"code": "BAD_REQUEST"}}
    monkeypatch.setattr(module, "call", fake_call)
    with pytest.raises(RuntimeError, match="login blocked"):
        module.login("md.maker", "fake-password", "fake-mfa", "fake-token")
    assert calls == ["/api/admin/auth/login"]


def test_isolated_bulk_happy_path_with_independent_rejection(monkeypatch):
    """Exercise the entire --submit orchestration without a network or real secrets."""
    import copy
    import sys

    monkeypatch.setenv("M3_UAT_TOKEN", "fake-uat-token")
    monkeypatch.setenv("M3_MAKER_PASSWORD", "fake")
    monkeypatch.setenv("M3_MAKER_MFA", "000000")
    monkeypatch.setenv("M3_CHECKER_PASSWORD", "fake")
    monkeypatch.setenv("M3_CHECKER_MFA", "000000")
    monkeypatch.setattr(sys, "argv", ["m3_isolated_atomic_bulk_uat.py", "--submit"])

    seen = {"submit": 0, "rejections": [], "maker_denied": 0, "logouts": []}
    original = {}

    def fake_call(path, *, method="GET", token=None, session=None,
                  body=None, extra_headers=None):
        if path == "/api/v1/health":
            return 200, {"sandbox_only": True, "production_promoted": False}
        if path == "/api/clx016/readiness":
            return 200, {"production_traffic": "OFF", "live_providers": "OFF",
                         "real_money": "OFF"}
        if path == "/api/admin/auth/login":
            user = body["username"]
            return 200, {"session_token": "session-" + user}
        if path == "/api/admin/auth/whoami":
            username = session.removeprefix("session-")
            roles = ["MASTER_DATA"] if username == "md.maker" else ["MASTER_DATA_MANAGER"]
            return 200, {"username": username, "roles": roles}
        if path == "/api/admin/auth/logout":
            seen["logouts"].append(session)
            return 200, {}
        if path == "/api/masterdata/bulk/submit-atomic":
            assert session == "session-md.maker"
            assert extra_headers["Idempotency-Key"].startswith("m3-uat-")
            seen["submit"] += 1
            if seen["submit"] == 1:
                original["body"] = copy.deepcopy(body)
                original["result"] = {
                    "atomic": True, "pending": 2,
                    "rows": [{"record_key": row["record_key"],
                              "change_ref": "MDC-" + str(index) + "AABBCC"}
                             for index, row in enumerate(body["rows"])]
                }
                return 200, copy.deepcopy(original["result"])
            if body != original["body"]:
                return 409, {"detail": {"code": "IDEMPOTENCY_CONFLICT"}}
            return 200, copy.deepcopy(original["result"])
        if path.startswith("/api/masterdata/bulk/submit-atomic/recovery?"):
            assert session == "session-md.maker"
            return 200, copy.deepcopy(original["result"])
        if path.startswith("/api/masterdata/changes/") and path.endswith("/decision"):
            assert body["decision"] == "REJECT"
            ref = path.split("/")[4]
            if session == "session-md.maker":
                seen["maker_denied"] += 1
                return 403, {"detail": {"code": "FOUR_EYES_REQUIRED"}}
            assert session == "session-md.checker"
            seen["rejections"].append(ref)
            return 200, {"status": "REJECTED"}
        raise AssertionError("Unexpected API operation: " + path)

    monkeypatch.setattr(module, "call", fake_call)
    assert module.main() == 0
    assert seen["submit"] == 3
    assert seen["maker_denied"] == 2
    assert set(seen["rejections"]) == {"MDC-0AABBCC", "MDC-1AABBCC"}
    assert seen["logouts"] == ["session-md.maker", "session-md.checker"]


def test_bulk_replay_failure_rejects_only_own_pending_changes(monkeypatch):
    """A failed replay must clean up both new UAT refs, never existing records."""
    import sys

    for name in ("M3_UAT_TOKEN", "M3_MAKER_PASSWORD", "M3_MAKER_MFA",
                 "M3_CHECKER_PASSWORD", "M3_CHECKER_MFA"):
        monkeypatch.setenv(name, "synthetic")
    monkeypatch.setattr(sys, "argv", ["m3_uat", "--submit"])
    rejected = []
    logouts = []
    submissions = []

    def fake_login(username, password, mfa, token):
        return "session-" + username, (["MASTER_DATA"] if username == "md.maker"
                                         else ["MASTER_DATA_MANAGER"])

    def fake_call(path, *, method="GET", token=None, session=None,
                  body=None, extra_headers=None):
        if path == "/api/v1/health":
            return 200, {"sandbox_only": True, "production_promoted": False}
        if path == "/api/clx016/readiness":
            return 200, {"production_traffic": "OFF", "live_providers": "OFF",
                         "real_money": "OFF"}
        if path == "/api/masterdata/bulk/submit-atomic":
            submissions.append(path)
            if len(submissions) == 1:
                return 200, {"atomic": True, "pending": 2, "rows": [
                    {"record_key": row["record_key"], "change_ref": "MDC-" + str(i) + "AABBCC"}
                    for i, row in enumerate(body["rows"])
                ]}
            return 500, {"detail": {"code": "SIMULATED_FAILURE"}}
        if path.startswith("/api/masterdata/changes/") and path.endswith("/decision"):
            assert session == "session-md.checker"
            assert body["decision"] == "REJECT"
            rejected.append(path.split("/")[4])
            return 200, {"status": "REJECTED"}
        if path == "/api/admin/auth/logout":
            logouts.append(session)
            return 200, {}
        raise AssertionError(path)

    monkeypatch.setattr(module, "login", fake_login)
    monkeypatch.setattr(module, "call", fake_call)
    with pytest.raises(RuntimeError, match="Replay failed"):
        module.main()
    assert rejected == ["MDC-0AABBCC", "MDC-1AABBCC"]
    assert logouts == ["session-md.maker", "session-md.checker"]
    assert len(submissions) == 2


def test_checker_login_failure_prevents_any_bulk_submission(monkeypatch):
    """Never create pending changes when independent Checker is unavailable."""
    import sys

    for name in ("M3_UAT_TOKEN", "M3_MAKER_PASSWORD", "M3_MAKER_MFA",
                 "M3_CHECKER_PASSWORD", "M3_CHECKER_MFA"):
        monkeypatch.setenv(name, "synthetic")
    monkeypatch.setattr(sys, "argv", ["m3_uat", "--submit"])
    operations = []

    def fake_login(username, password, mfa, token):
        if username == "md.checker":
            raise RuntimeError("simulated Checker authentication failure")
        return "maker-session", ["MASTER_DATA"]

    def fake_call(path, *, method="GET", token=None, session=None,
                  body=None, extra_headers=None):
        operations.append(path)
        if path == "/api/v1/health":
            return 200, {"sandbox_only": True, "production_promoted": False}
        if path == "/api/clx016/readiness":
            return 200, {"production_traffic": "OFF", "live_providers": "OFF",
                         "real_money": "OFF"}
        if path == "/api/admin/auth/logout":
            assert session == "maker-session"
            return 200, {}
        raise AssertionError("Unexpected call before Checker authentication: " + path)

    monkeypatch.setattr(module, "login", fake_login)
    monkeypatch.setattr(module, "call", fake_call)
    with pytest.raises(RuntimeError, match="Checker authentication failure"):
        module.main()
    assert operations == ["/api/v1/health", "/api/clx016/readiness",
                          "/api/admin/auth/logout"]


@pytest.mark.parametrize("unsafe_health,unsafe_readiness", [
    ({"sandbox_only": False, "production_promoted": False}, None),
    ({"sandbox_only": True, "production_promoted": True}, None),
    (None, {"production_traffic": "ON", "live_providers": "OFF", "real_money": "OFF"}),
    (None, {"production_traffic": "OFF", "live_providers": "ON", "real_money": "OFF"}),
    (None, {"production_traffic": "OFF", "live_providers": "OFF", "real_money": "ON"}),
])
def test_unsafe_runtime_flags_block_login_and_bulk(
        monkeypatch, unsafe_health, unsafe_readiness):
    """Fail closed before any authentication or write if runtime gates are unsafe."""
    import sys

    monkeypatch.setenv("M3_UAT_TOKEN", "synthetic")
    monkeypatch.setattr(sys, "argv", ["m3_uat", "--submit"])
    seen = []

    def fake_call(path, **kwargs):
        seen.append(path)
        if path == "/api/v1/health":
            return 200, unsafe_health or {"sandbox_only": True,
                                           "production_promoted": False}
        if path == "/api/clx016/readiness":
            return 200, unsafe_readiness or {
                "production_traffic": "OFF", "live_providers": "OFF",
                "real_money": "OFF"}
        raise AssertionError("Authentication or write must not occur: " + path)

    monkeypatch.setattr(module, "call", fake_call)
    with pytest.raises(RuntimeError, match="isolated|safety flags"):
        module.main()
    assert all("/auth/" not in item and "/bulk/" not in item for item in seen)


@pytest.mark.parametrize("bad_role_user", ["md.maker", "md.checker"])
def test_role_mismatch_blocks_bulk_and_logs_out_both(monkeypatch, bad_role_user):
    """Both independent identities must have precisely the required UAT role."""
    import sys
    for name in ("M3_UAT_TOKEN", "M3_MAKER_PASSWORD", "M3_MAKER_MFA",
                 "M3_CHECKER_PASSWORD", "M3_CHECKER_MFA"):
        monkeypatch.setenv(name, "synthetic")
    monkeypatch.setattr(sys, "argv", ["m3_uat", "--submit"])
    events = []

    def fake_login(username, password, mfa, token):
        roles = ["MASTER_DATA"] if username == "md.maker" else ["MASTER_DATA_MANAGER"]
        if username == bad_role_user:
            roles = ["SUPER_ADMIN"]
        return "session-" + username, roles

    def fake_call(path, **kwargs):
        events.append(path)
        if path == "/api/v1/health":
            return 200, {"sandbox_only": True, "production_promoted": False}
        if path == "/api/clx016/readiness":
            return 200, {"production_traffic": "OFF", "live_providers": "OFF",
                         "real_money": "OFF"}
        if path == "/api/admin/auth/logout":
            return 200, {}
        raise AssertionError("Unexpected write or auth request: " + path)

    monkeypatch.setattr(module, "login", fake_login)
    monkeypatch.setattr(module, "call", fake_call)
    with pytest.raises(RuntimeError, match="role mismatch"):
        module.main()
    assert events.count("/api/admin/auth/logout") == 2
    assert all("/bulk/" not in event for event in events)


@pytest.mark.parametrize("value", ["", " leading", "trailing ", "line\\nfeed", "tab\\tchar"])
def test_malformed_uat_password_rejected(value):
    with pytest.raises(RuntimeError):
        module.credential_diagnostics(value)


def test_valid_uat_password_passes_preflight():
    assert module.credential_diagnostics("Example_CaseSensitive_83!") is None


def test_malformed_password_blocks_login_before_http(monkeypatch):
    def should_not_call(*args, **kwargs):
        raise AssertionError("Unexpected HTTP request")
    monkeypatch.setattr(module, "call", should_not_call)
    with pytest.raises(RuntimeError):
        module.login("md.maker", " trailing", "000000", "synthetic")
