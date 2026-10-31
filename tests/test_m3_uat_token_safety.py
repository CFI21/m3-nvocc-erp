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
