"""Safety tests for isolated IAM provisioning; do not connect to a database."""
import pytest
from app import uat_iam_provision as u


def test_requires_explicit_authorization(monkeypatch):
    monkeypatch.delenv("M3_UAT_PROVISION_IAM", raising=False)
    monkeypatch.setattr(u.db, "_uat_target_approved", lambda: True)
    with pytest.raises(RuntimeError, match="NOT_AUTHORIZED"):
        u.provision()


def test_requires_independent_long_secrets(monkeypatch):
    monkeypatch.setenv("M3_UAT_PROVISION_IAM", "YES")
    monkeypatch.setattr(u.db, "_uat_target_approved", lambda: True)
    monkeypatch.setenv("M3_UAT_MAKER_PASSWORD", "too-short")
    monkeypatch.setenv("M3_UAT_CHECKER_PASSWORD", "different-not-enough")
    with pytest.raises(RuntimeError, match="UNIQUE_STRONG_PASSWORDS_REQUIRED"):
        u.provision()
    monkeypatch.setenv("M3_UAT_MAKER_PASSWORD", "unique-password-one-2036")
    monkeypatch.setenv("M3_UAT_CHECKER_PASSWORD", "unique-password-one-2036")
    with pytest.raises(RuntimeError, match="UNIQUE_STRONG_PASSWORDS_REQUIRED"):
        u.provision()


def test_never_overwrites_existing_iam(monkeypatch):
    monkeypatch.setenv("M3_UAT_PROVISION_IAM", "YES")
    monkeypatch.setattr(u.db, "_uat_target_approved", lambda: True)
    monkeypatch.setenv("M3_UAT_MAKER_PASSWORD", "different-secret-for-maker-123")
    monkeypatch.setenv("M3_UAT_CHECKER_PASSWORD", "different-secret-for-checker-456")
    class Row:
        def fetchone(self): return {"id": 1}
    class DB:
        closed = False
        def execute(self, statement, params=None):
            if statement == "SELECT 1 FROM iam_users LIMIT 1": return Row()
            if statement == "ROLLBACK": return Row()
            raise AssertionError("Attempted to modify existing database")
        def close(self): self.closed = True
    c = DB()
    monkeypatch.setattr(u.db, "connect", lambda: c)
    with pytest.raises(RuntimeError, match="ALREADY_INITIALIZED"):
        u.provision()
    assert c.closed
