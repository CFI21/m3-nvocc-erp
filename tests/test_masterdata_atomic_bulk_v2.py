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



def test_identical_request_replays_without_creating_more_changes():
    from app.masterdata import h, j
    request_body=request()
    digest=h(j({"domain":request_body.domain,"rows":[r.model_dump() for r in request_body.rows]}))
    conn=MagicMock()
    conn.execute.return_value.fetchone.return_value={
        "request_hash":digest,
        "response_json":j({"domain":"agent","total":2,"pending":2,"rows":[],"atomic":True}),
    }
    a,b,c,d=common(conn)
    with a,b,c,d,patch("app.masterdata._create_change_worker") as worker:
        result=bulk_submit_atomic(request_body,"uattest-replay-001","session")
    assert result["atomic"] is True
    worker.assert_not_called()
    statements=[call.args[0] for call in conn.execute.call_args_list]
    assert statements.count("COMMIT")==1
    assert not any("INSERT INTO md_bulk_idempotency" in item for item in statements)


def test_reused_key_with_different_payload_fails_closed():
    conn=MagicMock()
    conn.execute.return_value.fetchone.return_value={"request_hash":"not-the-same"}
    a,b,c,d=common(conn)
    with a,b,c,d,patch("app.masterdata._create_change_worker") as worker:
        with pytest.raises(HTTPException) as exc:
            bulk_submit_atomic(request(),"uattest-conflict-001","session")
    assert exc.value.status_code==409
    assert exc.value.detail["code"]=="BULK_IDEMPOTENCY_PAYLOAD_CONFLICT"
    worker.assert_not_called()
    assert "ROLLBACK" in [call.args[0] for call in conn.execute.call_args_list]


def test_missing_ledger_migration_prevents_any_row_write():
    conn=MagicMock()
    a,b,c,_=common(conn)
    with a,b,c,patch("app.db.table_exists",return_value=False),patch("app.masterdata._create_change_worker") as worker:
        with pytest.raises(HTTPException) as exc:
            bulk_submit_atomic(request(),"uattest-nomigration-001","session")
    assert exc.value.status_code==503
    assert exc.value.detail["code"]=="BULK_LEDGER_MIGRATION_REQUIRED"
    worker.assert_not_called()


def test_recovery_is_scoped_to_authenticated_maker():
    from app.masterdata import bulk_atomic_recovery
    conn=MagicMock()
    conn.execute.return_value.fetchone.return_value={"response_json":'{"atomic":true,"pending":2}'}
    a,b,c,d=common(conn)
    with a,b,c,d:
        result=bulk_atomic_recovery("uattest-recovery-001","session")
    assert result=={"atomic":True,"pending":2}
    matching=[call for call in conn.execute.call_args_list if
              "SELECT response_json FROM md_bulk_idempotency" in call.args[0]]
    assert len(matching)==1
    assert matching[0].args[1]==("maker-1","RTM","uattest-recovery-001")
