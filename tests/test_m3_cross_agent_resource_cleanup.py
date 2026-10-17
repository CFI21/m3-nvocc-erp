"""Denied cross-agent reads must close their database connection."""
import pytest
from fastapi import HTTPException
from app import main


class Connection:
    closed=False
    def execute(self, query, args):
        return self
    def fetchone(self):
        return {"job_ref":"00001"}
    def close(self):
        self.closed=True


def test_denied_cross_agent_record_read_closes_connection(monkeypatch):
    conn=Connection()
    monkeypatch.setattr(main,"connect",lambda:conn)
    monkeypatch.setattr(main,"bulk_permission_enabled",lambda:True)
    monkeypatch.setattr(main,"governed_job_actor",
                        lambda *_args,**_kwargs: (_ for _ in ()).throw(
                            HTTPException(403,{"code":"GOVERNED_JOB_ROLE_NOT_GRANTED"})))
    with pytest.raises(HTTPException) as exc:
        main.get_record("booking",1,x_m3_session="session")
    assert exc.value.status_code==403
    assert conn.closed


def test_not_found_record_closes_connection(monkeypatch):
    class Missing(Connection):
        def fetchone(self): return None
    conn=Missing()
    monkeypatch.setattr(main,"connect",lambda:conn)
    monkeypatch.setattr(main,"bulk_permission_enabled",lambda:True)
    with pytest.raises(HTTPException) as exc:
        main.get_record("booking",9876,x_m3_session="session")
    assert exc.value.status_code==404
    assert conn.closed


def test_list_requires_session_and_closes_connection(monkeypatch):
    conn=Connection()
    monkeypatch.setattr(main,"connect",lambda:conn)
    monkeypatch.setattr(main,"bulk_permission_enabled",lambda:True)
    with pytest.raises(HTTPException) as exc:
        main.list_records("booking",x_m3_session=None)
    assert exc.value.status_code==401
    assert conn.closed


def test_list_invalid_session_closes_connection(monkeypatch):
    conn=Connection()
    monkeypatch.setattr(main,"connect",lambda:conn)
    monkeypatch.setattr(main,"bulk_permission_enabled",lambda:True)
    monkeypatch.setattr(main,"iam_session",lambda *_args: (_ for _ in ()).throw(
        HTTPException(401,{"code":"SESSION_INVALID"})))
    with pytest.raises(HTTPException) as exc:
        main.list_records("booking",x_m3_session="invalid")
    assert exc.value.status_code==401
    assert conn.closed
