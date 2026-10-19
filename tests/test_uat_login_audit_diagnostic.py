from pathlib import Path

def test_isolated_diagnostic_returns_safe_admin_login_outcomes():
    src = Path("app/admin.py").read_text()
    section = src.split("def isolated_uat_diagnostic(", 1)[1].split("class UATAdminSetup", 1)[0]
    assert "X-M3-UAT-Diagnostic-Token" in section
    assert "secrets.compare_digest(configured, x_m3_uat_diagnostic_token)" in section
    assert "FROM iam_login_audit" in section
    assert "'recent_login_outcomes':recent_outcomes" in section
    assert "password_hash" not in section
    assert "session_token" not in section
