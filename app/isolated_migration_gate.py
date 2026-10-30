"""Read-only, fail-closed P0 data gate for isolated M3 migration.

Does not mutate data or authorize a migration. A separate independent checker
must resolve pending request collisions through the governed application.
"""


def pending_collisions(conn):
    rows=conn.execute("""
        SELECT domain, LOWER(TRIM(record_key)) AS normalized_key,
               COUNT(*) AS pending_count
        FROM md_change_requests
        WHERE status='PENDING'
        GROUP BY domain, LOWER(TRIM(record_key))
        HAVING COUNT(*) > 1
        ORDER BY domain, normalized_key
    """).fetchall()
    return [dict(row) for row in rows]


def master_key_collisions(conn):
    rows=conn.execute("""
        SELECT domain, LOWER(TRIM(record_key)) AS normalized_key,
               COUNT(*) AS active_count
        FROM md_records
        WHERE status='ACTIVE'
        GROUP BY domain, LOWER(TRIM(record_key))
        HAVING COUNT(*) > 1
        ORDER BY domain, normalized_key
    """).fetchall()
    return [dict(row) for row in rows]


def migration_data_preflight(conn):
    """Report facts only. A ready report does not authorize DDL."""
    pending=pending_collisions(conn)
    active=master_key_collisions(conn)
    return {'ready':not pending and not active,
            'pending_collisions':pending,'active_collisions':active}


def require_migration_data_preflight(conn):
    report=migration_data_preflight(conn)
    if not report['ready']:
        raise RuntimeError('ISOLATED_MIGRATION_DATA_GATE_BLOCKED')
    return report
