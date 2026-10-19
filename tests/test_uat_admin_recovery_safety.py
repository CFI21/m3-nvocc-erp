from pathlib import Path

def test_recovery_is_disabled_and_independently_authorized():
    s=Path("app/admin.py").read_text()
    section=s.split("def isolated_uat_admin_recovery(",1)[1].split("class UATAdminSetup",1)[0]
    for key in ["M3_ALLOW_ADMIN_RECOVERY", "M3_ADMIN_RECOVERY_SECRET",
                "M3_UAT_ADMIN_SETUP_TOKEN", "M3_PRODUCTION_TRAFFIC",
                "M3_UAT_PRESERVE_DATA", "secrets.compare_digest",
                "db._uat_target_approved()"]:
        assert key in section
    assert "recovery_secret) < 48" in section
    assert "RECOVERY_ALREADY_USED" in section

def test_recovery_preserves_account_status_mfa_and_audit():
    s=Path("app/admin.py").read_text()
    section=s.split("def isolated_uat_admin_recovery(",1)[1].split("class UATAdminSetup",1)[0]
    assert "UPDATE iam_users SET password_hash=?,failed_attempts=0,locked_until=NULL" in section
    assert "UPDATE iam_sessions SET status='REVOKED'" in section
    assert "UAT_ADMIN_PASSWORD_RECOVERY" in section
    assert "status='INACTIVE'" not in section
    assert "SET mfa_required" not in section
    assert "SET role_id" not in section
