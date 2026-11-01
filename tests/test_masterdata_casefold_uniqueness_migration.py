"""Contract tests for a staged, non-destructive master-data uniqueness migration.

No PostgreSQL session or live business data is modified by this test.
"""
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "M3_MASTERDATA_002_casefold_uniqueness_guard.sql"


def test_migration_refuses_unreviewed_duplicates_and_is_transactional():
    sql = MIGRATION.read_text()
    assert sql.count("CREATE UNIQUE INDEX IF NOT EXISTS") == 2
    assert sql.index("BEGIN;") < sql.index("LOCK TABLE")
    assert sql.index("LOCK TABLE") < sql.index("DO $m3_casefold_preflight$")
    assert sql.index("DO $m3_casefold_preflight$") < sql.index("CREATE UNIQUE INDEX")
    assert sql.rstrip().endswith("COMMIT;")
    assert "M3_MASTER_CASEFOLD_CONFLICT" in sql
    assert "M3_PENDING_CASEFOLD_CONFLICT" in sql
    assert "WHERE status = 'PENDING'" in sql
    assert "lower(domain), lower(record_key)" in sql
    assert "SHARE ROW EXCLUSIVE MODE" in sql


def test_equivalent_indexes_reject_pending_collisions_but_keep_audit_history():
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE md_change_requests(domain text,record_key text,status text);"
        "CREATE TABLE md_records(domain text,record_key text,status text);"
        "CREATE UNIQUE INDEX pending_casefold ON md_change_requests"
        "(lower(domain),lower(record_key)) WHERE status='PENDING';"
        "CREATE UNIQUE INDEX record_casefold ON md_records"
        "(lower(domain),lower(record_key));"
    )
    db.execute("INSERT INTO md_change_requests VALUES('port','AFHEA','PENDING')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO md_change_requests VALUES('PORT','afhea','PENDING')")
    db.execute("UPDATE md_change_requests SET status='REJECTED' WHERE record_key='AFHEA'")
    db.execute("INSERT INTO md_change_requests VALUES('port','afhea','PENDING')")
    db.execute("INSERT INTO md_change_requests VALUES('port','AFHEA','APPROVED')")
    assert db.execute("SELECT count(*) FROM md_change_requests").fetchone()[0] == 3
    db.execute("INSERT INTO md_records VALUES('port','AFHEA','INACTIVE')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO md_records VALUES('PORT','afhea','ACTIVE')")
    db.close()
