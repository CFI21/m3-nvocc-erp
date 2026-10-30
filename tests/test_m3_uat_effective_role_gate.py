"""Isolated cutover readiness enforces effective IAM authorization, not role-row presence.

Fixtures use a disposable SQLite file; no production or Neon writes.
"""
import datetime
import sqlite3

import pytest

from app import uat_bootstrap


@pytest.mark.parametrize(
    "invalid",
    (
        None,
        "maker_inactive",
        "checker_inactive",
        "maker_role_inactive",
        "checker_role_inactive",
        "maker_grant_inactive",
        "checker_grant_inactive",
        "maker_grant_future",
        "checker_grant_future",
        "maker_grant_expired",
        "checker_grant_expired",
        "unexpected_extra_grant",
    ),
)
def test_cutover_requires_currently_effective_independent_roles(
    tmp_path, monkeypatch, invalid
):
    db_path = tmp_path / "effective-role-gate.sqlite3"

    def connect():
        conn = sqlite3.connect(str(db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    current = datetime.datetime.now(datetime.timezone.utc)
    before = (current - datetime.timedelta(days=2)).isoformat()
    after = (current + datetime.timedelta(days=2)).isoformat()

    c = connect()
    c.executescript("""
        CREATE TABLE md_change_requests(status TEXT);
        CREATE TABLE md_records(domain TEXT);
        CREATE TABLE iam_users(id INTEGER PRIMARY KEY,username TEXT,status TEXT);
        CREATE TABLE iam_roles(id INTEGER PRIMARY KEY,role_code TEXT,status TEXT);
        CREATE TABLE iam_user_roles(id INTEGER PRIMARY KEY,user_id INTEGER,role_id INTEGER,
            status TEXT,valid_from TEXT,valid_to TEXT);
    """)
    c.executemany("INSERT INTO iam_users VALUES(?,?,?)", [
        (1, "md.maker", "ACTIVE"),
        (2, "md.checker", "ACTIVE"),
    ])
    c.executemany("INSERT INTO iam_roles VALUES(?,?,?)", [
        (1, "MASTER_DATA", "ACTIVE"),
        (2, "MASTER_DATA_MANAGER", "ACTIVE"),
    ])
    c.executemany("INSERT INTO iam_user_roles VALUES(?,?,?,?,?,?)", [
        (1, 1, 1, "ACTIVE", before, after),
        (2, 2, 2, "ACTIVE", before, after),
    ])
    if invalid in ("maker_inactive", "checker_inactive"):
        user = 1 if invalid.startswith("maker") else 2
        c.execute("UPDATE iam_users SET status='INACTIVE' WHERE id=?", (user,))
    elif invalid in ("maker_role_inactive", "checker_role_inactive"):
        role = 1 if invalid.startswith("maker") else 2
        c.execute("UPDATE iam_roles SET status='INACTIVE' WHERE id=?", (role,))
    elif invalid in ("maker_grant_inactive", "checker_grant_inactive"):
        grant = 1 if invalid.startswith("maker") else 2
        c.execute("UPDATE iam_user_roles SET status='INACTIVE' WHERE id=?", (grant,))
    elif invalid in ("maker_grant_future", "checker_grant_future"):
        grant = 1 if invalid.startswith("maker") else 2
        c.execute("UPDATE iam_user_roles SET valid_from=? WHERE id=?", (after, grant))
    elif invalid in ("maker_grant_expired", "checker_grant_expired"):
        grant = 1 if invalid.startswith("maker") else 2
        c.execute("UPDATE iam_user_roles SET valid_to=? WHERE id=?", (before, grant))
    elif invalid == "unexpected_extra_grant":
        c.execute(
            "INSERT INTO iam_user_roles VALUES(?,?,?,?,?,?)",
            (3, 1, 2, "ACTIVE", before, after),
        )
    c.close()

    monkeypatch.setenv("M3_UAT_PRESERVE_DATA", "1")
    monkeypatch.setattr(
        uat_bootstrap.db, "DATABASE_URL", "postgresql://mock@isolated.example/neondb"
    )
    monkeypatch.setattr(uat_bootstrap.db, "_uat_target_approved", lambda: True)
    monkeypatch.setattr(uat_bootstrap.db, "connect", connect)
    monkeypatch.setattr(uat_bootstrap.db, "table_exists", lambda conn, name: True)

    if invalid is None:
        ready = uat_bootstrap.ensure_uat_ready()
        assert ready == {
            "database": "isolated-neon-uat",
            "schema": "ready",
            "pending_approvals": 0,
            "master_agents": 0,
        }
    else:
        with pytest.raises(RuntimeError, match="UAT_IAM_ROLE_SEPARATION_REQUIRED"):
            uat_bootstrap.ensure_uat_ready()

    c = connect()
    assert c.execute("SELECT COUNT(*) FROM iam_user_roles").fetchone()[0] == (
        3 if invalid == "unexpected_extra_grant" else 2
    )
    assert c.execute("SELECT COUNT(*) FROM md_change_requests").fetchone()[0] == 0
    c.close()
