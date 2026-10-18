"""Duplicate master names are canonicalized without altering stored display values."""
import sqlite3
from app import masterdata


def test_active_duplicate_ignores_case_and_whitespace():
    db=sqlite3.connect(":memory:")
    db.row_factory=sqlite3.Row
    masterdata.init_schema(db)
    db.execute(
        "INSERT INTO md_records(domain,record_key,display_name,payload_json,status,created_at,updated_at) "
        "VALUES ('carrier','C1','North  Sea Carrier','{}','ACTIVE','now','now')"
    )
    assert masterdata.duplicate_name(db, "carrier", " NORTH sea   CARRIER ")["record_key"]=="C1"
    assert masterdata.duplicate_name(db, "carrier", "North Sea Carrier", exclude_key="C1") is None
    db.close()


def test_pending_duplicate_ignores_case_and_whitespace():
    db=sqlite3.connect(":memory:")
    db.row_factory=sqlite3.Row
    masterdata.init_schema(db)
    db.execute(
        "INSERT INTO md_change_requests(change_ref,domain,record_key,operation,payload_json,reason,maker,created_at) "
        "VALUES ('R1','carrier','C2','CREATE',?,'test','maker','now')",
        ('{"name":"North  Sea Carrier"}',),
    )
    assert masterdata.pending_duplicate_name(db, "carrier", " north SEA carrier ")["record_key"]=="C2"
    db.close()
