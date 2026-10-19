"""Isolated SQLite regression for Master Data Maker/Checker governance.

Uses a temporary database, synthetic identities and no Render/Neon credentials.
Run: pytest -q tests/test_m3_maker_checker_regression.py
"""
import sqlite3
import pytest
from fastapi import HTTPException

from app import masterdata as md


@pytest.fixture
def harness(tmp_path, monkeypatch):
    path = tmp_path / "maker_checker.sqlite3"
    def connect():
        c = sqlite3.connect(path, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c
    c = connect()
    md.init_schema(c)
    c.close()
    monkeypatch.setattr(md, "connect", connect)
    identities = {
        "maker": ({"user_ref": "USER-MAKER", "username": "md.maker"}, {"MASTER_DATA"}),
        "checker": ({"user_ref": "USER-CHECKER", "username": "md.checker"}, {"MASTER_DATA_MANAGER"}),
        "same-checker": ({"user_ref": "USER-MAKER", "username": "md.maker"}, {"MASTER_DATA_MANAGER"}),
    }
    monkeypatch.setattr(md, "authenticated_actor", lambda c, token: identities[token])
    return connect


def _pending(connect, ref="MDC-UAT-01", key="UAT-UNIT-01"):
    c = connect()
    c.execute("""INSERT INTO md_change_requests
      (change_ref,domain,record_key,operation,payload_json,reason,maker,created_at,base_version)
      VALUES (?, 'unit', ?, 'CREATE', ?, 'isolated regression', 'USER-MAKER', ?, 0)""",
      (ref, key, '{"name":"UAT regression unit"}', md.now()))
    c.close()


def _error_code(exc):
    assert isinstance(exc.value, HTTPException)
    return exc.value.detail.get("code") if isinstance(exc.value.detail, dict) else exc.value.detail


def test_self_approval_denied_and_pending_unchanged(harness):
    _pending(harness)
    with pytest.raises(HTTPException) as exc:
        md.decide("MDC-UAT-01", md.Decision(decision="APPROVE"), x_m3_session="same-checker")
    assert exc.value.status_code == 409
    assert _error_code(exc) == "FOUR_EYES_VIOLATION"
    c=harness()
    assert c.execute("SELECT status FROM md_change_requests WHERE change_ref='MDC-UAT-01'").fetchone()["status"]=="PENDING"
    assert c.execute("SELECT COUNT(*) AS n FROM md_records").fetchone()["n"]==0
    c.close()


def test_maker_without_checker_role_cannot_approve(harness):
    _pending(harness)
    with pytest.raises(HTTPException) as exc:
        md.decide("MDC-UAT-01", md.Decision(decision="APPROVE"), x_m3_session="maker")
    assert exc.value.status_code==403
    assert _error_code(exc)=="CHECKER_PERMISSION_DENIED"


def test_checker_rejection_audited_and_repeat_blocked(harness):
    _pending(harness)
    result=md.decide("MDC-UAT-01", md.Decision(decision="REJECT", comment="UAT rejection"), x_m3_session="checker")
    assert result["status"]=="REJECTED"
    c=harness()
    assert c.execute("SELECT status,checker FROM md_change_requests WHERE change_ref='MDC-UAT-01'").fetchone()["checker"]=="USER-CHECKER"
    assert c.execute("SELECT COUNT(*) AS n FROM md_audit WHERE action='CHANGE_REJECT'").fetchone()["n"]==1
    assert c.execute("SELECT COUNT(*) AS n FROM md_records").fetchone()["n"]==0
    c.close()
    with pytest.raises(HTTPException) as exc:
        md.decide("MDC-UAT-01", md.Decision(decision="APPROVE"), x_m3_session="checker")
    assert exc.value.status_code==409
    assert _error_code(exc)=="CHANGE_ALREADY_DECIDED"


def test_original_maker_can_withdraw_pending_change(harness):
    _pending(harness)
    result=md.withdraw_change("MDC-UAT-01", md.Withdrawal(reason="UAT withdrawal"), x_m3_session="maker")
    assert result["status"]=="WITHDRAWN"
    c=harness()
    assert c.execute("SELECT status FROM md_change_requests WHERE change_ref='MDC-UAT-01'").fetchone()["status"]=="WITHDRAWN"
    assert c.execute("SELECT COUNT(*) AS n FROM md_audit WHERE action='CHANGE_WITHDRAW'").fetchone()["n"]==1
    assert c.execute("SELECT COUNT(*) AS n FROM md_records").fetchone()["n"]==0
    c.close()
    with pytest.raises(HTTPException) as exc:
        md.decide("MDC-UAT-01", md.Decision(decision="APPROVE"), x_m3_session="checker")
    assert exc.value.status_code==409
    assert _error_code(exc)=="CHANGE_ALREADY_DECIDED"


def test_checker_cannot_withdraw_maker_request(harness):
    _pending(harness)
    with pytest.raises(HTTPException) as exc:
        md.withdraw_change("MDC-UAT-01", md.Withdrawal(reason="Unauthorized withdrawal"), x_m3_session="checker")
    assert exc.value.status_code==403
    assert _error_code(exc)=="WITHDRAW_MAKER_ONLY"
    c=harness()
    assert c.execute("SELECT status FROM md_change_requests WHERE change_ref='MDC-UAT-01'").fetchone()["status"]=="PENDING"
    c.close()
