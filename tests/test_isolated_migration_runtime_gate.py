"""Read-only migration gate regression tests."""
import sqlite3
import pytest
from app.isolated_migration_gate import migration_data_preflight, require_migration_data_preflight


def db():
    c=sqlite3.connect(':memory:')
    c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE md_change_requests (domain TEXT,record_key TEXT,status TEXT)')
    c.execute('CREATE TABLE md_records (domain TEXT,record_key TEXT,status TEXT)')
    return c


def test_afhea_casefold_duplicate_blocks_gate():
    c=db()
    c.executemany('INSERT INTO md_change_requests VALUES(?,?,?)',[
        ('port','AFHEA','PENDING'),('port',' afhea ','PENDING')])
    report=migration_data_preflight(c)
    assert report['ready'] is False
    assert report['pending_collisions'][0]['normalized_key']=='afhea'
    with pytest.raises(RuntimeError,match='ISOLATED_MIGRATION_DATA_GATE_BLOCKED'):
        require_migration_data_preflight(c)


def test_active_master_collision_blocks_gate():
    c=db()
    c.executemany('INSERT INTO md_records VALUES(?,?,?)',[
        ('port','AFHEA','ACTIVE'),('port','afhea','ACTIVE')])
    assert migration_data_preflight(c)['ready'] is False


def test_resolved_duplicate_passes_read_only_gate():
    c=db()
    c.executemany('INSERT INTO md_change_requests VALUES(?,?,?)',[
        ('port','AFHEA','PENDING'),('port','afhea','REJECTED')])
    assert require_migration_data_preflight(c)['ready'] is True
