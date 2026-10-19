"""Security checks for the UAT-only read-only diagnostic."""
from pathlib import Path

def test_diagnostic_requires_private_secret_and_isolated_postgres():
    source = Path("app/admin.py").read_text()
    assert "def isolated_uat_diagnostic(" in source
    assert "X-M3-UAT-Diagnostic-Token" in source
    assert "secrets.compare_digest(configured, x_m3_uat_diagnostic_token)" in source
    assert "not db._uat_target_approved()" in source
    assert "SELECT status,mfa_required FROM iam_users WHERE username='admin'" in source

def test_diagnostic_exposes_no_password_data():
    source = Path("app/admin.py").read_text()
    start=source.index("def isolated_uat_diagnostic(")
    end=source.index("class UATAdminRecovery",start)
    body=source[start:end]
    assert "password_hash" not in body
    assert "SELECT * FROM iam_users" not in body
    assert "'admin_password_initialized':None" in body
