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


def test_independent_checker_approves_and_versions_master(harness, monkeypatch):
    _pending(harness)
    # Operational projections are integration-tested separately; this test
    # isolates approval governance and version/audit persistence.
    monkeypatch.setattr(md, "sync_operational_projection", lambda *args, **kwargs: None)
    result=md.decide("MDC-UAT-01", md.Decision(decision="APPROVE", comment="UAT approved"), x_m3_session="checker")
    assert result=={"status":"APPROVED","version":1}
    c=harness()
    change=c.execute("SELECT status,maker,checker,applied_at FROM md_change_requests WHERE change_ref='MDC-UAT-01'").fetchone()
    assert change["status"]=="APPROVED"
    assert change["maker"]=="USER-MAKER"
    assert change["checker"]=="USER-CHECKER"
    assert change["applied_at"]
    master=c.execute("SELECT version,status,approved_by,display_name FROM md_records WHERE domain='unit' AND record_key='UAT-UNIT-01'").fetchone()
    assert master["version"]==1 and master["status"]=="ACTIVE"
    assert master["approved_by"]=="USER-CHECKER"
    assert master["display_name"]=="UAT regression unit"
    version=c.execute("SELECT version,changed_by,change_ref FROM md_versions WHERE domain='unit' AND record_key='UAT-UNIT-01'").fetchone()
    assert version["version"]==1
    assert version["changed_by"]=="USER-CHECKER"
    assert version["change_ref"]=="MDC-UAT-01"
    assert c.execute("SELECT COUNT(*) AS n FROM md_audit WHERE action='CHANGE_APPROVE_APPLY'").fetchone()["n"]==1
    c.close()
    with pytest.raises(HTTPException) as exc:
        md.decide("MDC-UAT-01", md.Decision(decision="APPROVE"), x_m3_session="checker")
    assert exc.value.status_code==409
    assert _error_code(exc)=="CHANGE_ALREADY_DECIDED"


def test_checker_recovery_disabled_by_default(monkeypatch):
    from app import admin
    monkeypatch.delenv("M3_ALLOW_CHECKER_RECOVERY", raising=False)
    # The flag must be evaluated before any database connection or credential update.
    with pytest.raises(HTTPException) as exc:
        admin.isolated_uat_checker_reset(
            admin.UATCheckerPasswordReset(new_password="A-placeholder-strong-password"),
            x_m3_session=None,
        )
    assert exc.value.status_code == 404
    assert _error_code(exc) == "NOT_FOUND"
