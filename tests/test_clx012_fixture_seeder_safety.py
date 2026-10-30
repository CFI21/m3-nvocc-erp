"""Tests for the additive, fail-closed CLX012 UAT fixture seeder."""
from unittest.mock import patch
import pytest

from scripts import seed_clx012_uat_fixtures as fixtures

class FakeConnection:
    def __init__(self, jobs=None):
        self.jobs = jobs or set()
        self.executed = []
    def execute(self, sql, params=()):
        self.executed.append(sql)
        if sql.startswith("SELECT id FROM jobs WHERE job_ref"):
            return Result({"id": 1} if params[0] in self.jobs else None)
        raise AssertionError("Dry-run must not issue SQL other than existence checks")

class Result:
    def __init__(self, row):
        self.row = row
    def fetchone(self):
        return self.row

def test_dry_run_does_not_write_to_database():
    conn = FakeConnection()
    with patch.object(fixtures.db, "table_exists", return_value=True):
        outcome = fixtures.seed(conn, apply=False)
    assert outcome["mode"] == "dry-run"
    assert outcome["would_create_jobs"] == ["50001", "50002", "50003", "50004", "50005"]
    assert all(statement.startswith("SELECT") for statement in conn.executed)

def test_partial_existing_jobs_are_rejected_without_writes():
    conn = FakeConnection({"50001"})
    with patch.object(fixtures.db, "table_exists", return_value=True):
        with pytest.raises(RuntimeError, match="Partial canonical job set"):
            fixtures.seed(conn, apply=False)
    assert all(statement.startswith("SELECT") for statement in conn.executed)

def test_missing_schema_is_rejected_without_writes():
    conn = FakeConnection()
    with patch.object(fixtures.db, "table_exists", side_effect=lambda _c, t: t != "jobs"):
        with pytest.raises(RuntimeError, match="Missing fixture schema"):
            fixtures.seed(conn, apply=False)
    assert conn.executed == []
