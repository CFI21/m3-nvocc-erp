"""Read-only CLX012 fixture preflight regression tests."""
from unittest.mock import patch
import pytest
from scripts.check_uat_clx012_fixtures import inspect, main

TABLES = {
    "jobs", "bookings", "customers", "agents", "workflow_states",
    "finance_states", "clx012_runs", "clx012_stage_events",
    "clx012_exceptions", "clx012_action_queue", "clx012_events",
}

class Result:
    def __init__(self, row):
        self.row = row
    def fetchone(self):
        return self.row

class FakeConnection:
    def __init__(self, job_rows):
        self.job_rows = job_rows
        self.queries = []
    def execute(self, sql, params):
        assert sql.lstrip().upper().startswith("SELECT")
        self.queries.append(params[0])
        return Result(self.job_rows.get(params[0]))

def complete(ref):
    return {"job_ref": ref, "booking_id": 1, "customer_id": 1,
            "agent_id": 1, "workflow_id": 1, "finance_id": 1}

def test_preflight_all_complete_is_read_only():
    conn = FakeConnection({str(n): complete(str(n)) for n in range(50001, 50006)})
    with patch("scripts.check_uat_clx012_fixtures.db.table_exists",
               side_effect=lambda _conn, name: name in TABLES):
        result = inspect(conn)
    assert result["ready_for_clx012"] is True
    assert conn.queries == list(result["expected_jobs"])

def test_preflight_reports_missing_and_broken_links():
    rows = {"50001": complete("50001"), "50002": complete("50002")}
    rows["50002"]["finance_id"] = None
    conn = FakeConnection(rows)
    with patch("scripts.check_uat_clx012_fixtures.db.table_exists",
               return_value=True):
        result = inspect(conn)
    assert result["missing_jobs"] == ["50003", "50004", "50005"]
    assert result["incomplete_jobs"] == [{"job_ref": "50002", "missing": ["finance_id"]}]
    assert result["ready_for_clx012"] is False

def test_preflight_missing_table_short_circuits():
    conn = FakeConnection({})
    with patch("scripts.check_uat_clx012_fixtures.db.table_exists",
               side_effect=lambda _conn, name: name != "jobs"):
        result = inspect(conn)
    assert result["missing_tables"] == ["jobs"]
    assert conn.queries == []

def test_preflight_rejects_unapproved_database_before_connect():
    with patch("scripts.check_uat_clx012_fixtures.db.using_postgres", return_value=True), \
         patch("scripts.check_uat_clx012_fixtures.db._uat_target_approved", return_value=False), \
         patch("scripts.check_uat_clx012_fixtures.db.connect") as connect:
        with pytest.raises(RuntimeError, match="Host-pinned"):
            main()
        connect.assert_not_called()
