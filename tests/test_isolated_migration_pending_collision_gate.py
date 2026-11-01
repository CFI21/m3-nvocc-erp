"""Day 1 isolated migration gate: read-only duplicate collision detection.

This module deliberately cannot approve changes, change IAM, or apply migrations.
"""
import sqlite3


DUPLICATE_PENDING_SQL = """
SELECT domain, LOWER(TRIM(record_key)) AS normalized_key, COUNT(*) AS duplicates
FROM md_change_requests
WHERE status='PENDING'
GROUP BY domain, LOWER(TRIM(record_key))
HAVING COUNT(*) > 1
ORDER BY domain, normalized_key
"""


def pending_collision_groups(conn):
    return [dict(zip(("domain", "normalized_key", "duplicates"), row))
            for row in conn.execute(DUPLICATE_PENDING_SQL).fetchall()]


def assert_isolated_migration_data_gate(conn):
    collisions = pending_collision_groups(conn)
    if collisions:
        raise RuntimeError("ISOLATED_MIGRATION_PENDING_DUPLICATES")
    return True


def test_duplicate_afhea_blocks_migration():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE md_change_requests(domain TEXT,record_key TEXT,status TEXT)")
    conn.executemany("INSERT INTO md_change_requests VALUES(?,?,?)", [
        ("port", "AFHEA", "PENDING"),
        ("port", "afhea", "PENDING"),
    ])
    assert pending_collision_groups(conn) == [
        {"domain": "port", "normalized_key": "afhea", "duplicates": 2}
    ]
    try:
        assert_isolated_migration_data_gate(conn)
    except RuntimeError as error:
        assert str(error) == "ISOLATED_MIGRATION_PENDING_DUPLICATES"
    else:
        raise AssertionError("Duplicate AFHEA should block migration")


def test_resolved_request_clears_collision():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE md_change_requests(domain TEXT,record_key TEXT,status TEXT)")
    conn.executemany("INSERT INTO md_change_requests VALUES(?,?,?)", [
        ("port", "AFHEA", "PENDING"),
        ("port", "afhea", "REJECTED"),
    ])
    assert assert_isolated_migration_data_gate(conn)


def test_single_pending_record_is_not_blocked():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE md_change_requests(domain TEXT,record_key TEXT,status TEXT)")
    conn.execute("INSERT INTO md_change_requests VALUES('port','AFHEA','PENDING')")
    assert assert_isolated_migration_data_gate(conn)
