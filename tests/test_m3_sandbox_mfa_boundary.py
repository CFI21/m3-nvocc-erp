"""Protect M3's isolated UAT-only fixed MFA challenge from production use."""

from app.admin import sandbox_mfa_allowed


def test_sandbox_mfa_allowed_only_when_production_traffic_off(monkeypatch):
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC', 'OFF')
    assert sandbox_mfa_allowed() is True
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC', 'off')
    assert sandbox_mfa_allowed() is True
    for value in ('ON', 'TRUE', '1', 'ENABLED', 'unknown'):
        monkeypatch.setenv('M3_PRODUCTION_TRAFFIC', value)
        assert sandbox_mfa_allowed() is False


def test_login_enforces_boundary_before_accepting_sandbox_mfa():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'app' / 'admin.py').read_text()
    assert "if u['mfa_required'] and not sandbox_mfa_allowed():" in source
    assert source.index("if u['mfa_required'] and not sandbox_mfa_allowed():") < source.index("if u['mfa_required'] and b.mfa_code!='123456':")
    assert "'SANDBOX_MFA_DISABLED'" in source
