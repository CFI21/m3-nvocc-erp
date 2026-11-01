"""Fail-closed isolated UAT startup tests without touching live databases."""
import pytest
from app import uat_bootstrap


def test_requires_preservation_mode(monkeypatch):
    monkeypatch.delenv("M3_UAT_PRESERVE_DATA", raising=False)
    with pytest.raises(RuntimeError, match="UAT_PRESERVATION_REQUIRED"):
        uat_bootstrap.ensure_uat_ready()


def test_rejects_ephemeral_sqlite(monkeypatch):
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA", "1")
    monkeypatch.setattr(uat_bootstrap.db, "DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="UAT_DURABLE_DATABASE_REQUIRED"):
        uat_bootstrap.ensure_uat_ready()


def test_rejects_unapproved_database(monkeypatch):
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA", "1")
    monkeypatch.setattr(uat_bootstrap.db, "DATABASE_URL", "postgresql://x:y@bad.invalid/uat")
    monkeypatch.setattr(uat_bootstrap.db, "_uat_target_approved", lambda: False)
    with pytest.raises(RuntimeError, match="UAT_NEON_TARGET_NOT_APPROVED"):
        uat_bootstrap.ensure_uat_ready()


def test_missing_schema_never_runs_seeds(monkeypatch):
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA", "1")
    monkeypatch.setattr(uat_bootstrap.db, "DATABASE_URL", "postgresql://x:y@safe.neon.tech/uat")
    monkeypatch.setattr(uat_bootstrap.db, "_uat_target_approved", lambda: True)
    class FakeConnection:
        closed = False
        def close(self): self.closed = True
    c = FakeConnection()
    monkeypatch.setattr(uat_bootstrap.db, "connect", lambda: c)
    monkeypatch.setattr(uat_bootstrap.db, "table_exists", lambda conn, table: False)
    with pytest.raises(RuntimeError, match="UAT_SCHEMA_MIGRATION_REQUIRED"):
        uat_bootstrap.ensure_uat_ready()
    assert c.closed
