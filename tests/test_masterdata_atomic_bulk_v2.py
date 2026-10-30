"""Atomic bulk v2 contract tests; no live services or persistent DB writes."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.masterdata import BulkRequest, BulkRow, bulk_submit_atomic


def request():
    return BulkRequest(domain="agent", rows=[
        BulkRow(record_key="UAT-A", operation="CREATE", payload={"name":"A"}, reason="test only"),
        BulkRow(record_key="UAT-B", operation="CREATE", payload={"name":"B"}, reason="test only"),
    ])


def common(conn):
    return (
        patch("app.masterdata.connect", return_value=conn),
        patch("app.masterdata.using_postgres", return_value=False),
        patch("app.masterdata.authenticated_actor",
              return_value=({"user_ref":"maker-1","office_code":"RTM"},{"MASTER_DATA"})),
        patch("app.db.table_exists", return_value=True),
    )


def test_atomic_bulk_commits_one_response_ledger():
    conn=MagicMock()
    conn.execute.return_value.fetchone.return_value=None
    a,b,c,d=common(conn)
    with a,b,c,d,patch("app.masterdata._create_change_worker",side_effect=[
        {"change_ref":"MDC-A"},{"change_ref":"MDC-B"}]) as worker:
        result=bulk_submit_atomic(request(),"uattest-unique-001","session")
    assert result["atomic"] is True
    assert result["pending"]==2
    assert worker.call_count==2
    assert all(call.kwargs["c"] is conn and call.kwargs["owns_transaction"] is False
               for call in worker.call_args_list)
    statements=[call.args[0] for call in conn.execute.call_args_list]
    assert statements.count("BEGIN IMMEDIATE")==1
    assert statements.count("COMMIT")==1
    assert "ROLLBACK" not in statements
    assert any("INSERT INTO md_bulk_idempotency" in item for item in statements)


def test_atomic_bulk_rolls_back_on_second_row_failure():
    conn=MagicMock()
    conn.execute.return_value.fetchone.return_value=None
    a,b,c,d=common(conn)
    with a,b,c,d,patch("app.masterdata._create_change_worker",side_effect=[
        {"change_ref":"MDC-A"},HTTPException(409,{"code":"PENDING_CHANGE_EXISTS"})]):
        with pytest.raises(HTTPException):
            bulk_submit_atomic(request(),"uattest-unique-002","session")
    statements=[call.args[0] for call in conn.execute.call_args_list]
    assert "ROLLBACK" in statements
    assert "COMMIT" not in statements
    assert not any("INSERT INTO md_bulk_idempotency" in item for item in statements)


def test_atomic_bulk_requires_scoped_key():
    with pytest.raises(HTTPException) as exc:
        bulk_submit_atomic(request(),None,"session")
    assert exc.value.status_code==422
    assert exc.value.detail["code"]=="BULK_IDEMPOTENCY_KEY_REQUIRED"
