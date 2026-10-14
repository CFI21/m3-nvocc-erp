"""Isolated UAT database allowlist guards; no network or database connection."""
import importlib

def test_neon_uat_needs_explicit_flag_and_exact_host(monkeypatch):
    from app import db
    monkeypatch.setattr(db, 'DATABASE_URL', 'postgresql://uat_user:dummy@ep-uat-check.aws-us-east-2.aws.neon.tech/m3_uat?sslmode=require')
    monkeypatch.setattr(db, 'M3_UAT_DB_HOST', 'ep-uat-check.aws-us-east-2.aws.neon.tech')
    monkeypatch.delenv('M3_UAT_PRESERVE_DATA', raising=False)
    assert not db.approved_database_target()
    monkeypatch.setenv('M3_UAT_PRESERVE_DATA', '1')
    assert db.approved_database_target()
    monkeypatch.setattr(db, 'M3_UAT_DB_HOST', 'other.neon.tech')
    assert not db.approved_database_target()
    monkeypatch.setattr(db, 'M3_UAT_DB_HOST', 'ep-uat-check.aws-us-east-2.aws.neon.tech')
    monkeypatch.setattr(db, 'DATABASE_URL', 'postgresql://uat_user:dummy@ep-uat-check.aws-us-east-2.aws.neon.tech.evil.example/m3_uat')
    assert not db.approved_database_target()
    monkeypatch.setattr(db, 'DATABASE_URL', 'postgresql://uat_user:dummy@db.ozupgknqaqgvprliewxe.supabase.co/m3_uat')
    assert not db._uat_target_approved()
    monkeypatch.setattr(db, 'DATABASE_URL', 'postgresql://uat_user:dummy@ep-uat-check.aws-us-east-2.aws.neon.tech/m3_uat')
    monkeypatch.setattr(db, 'M3_UAT_DB_HOST', '')
    assert not db.approved_database_target()
