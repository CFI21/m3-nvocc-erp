"""Isolated, synthetic end-to-end master-data UAT; no live credentials or DB writes."""

import sqlite3

import pytest
from fastapi import HTTPException

from app import masterdata


@pytest.fixture
def uat_db(tmp_path, monkeypatch):
    path = str(tmp_path / "masterdata_lifecycle_uat.db")

    def connect():
        # Production DB adapter commits each statement. Match that behavior
        # in the isolated SQLite acceptance fixture.
        conn = sqlite3.connect(path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    conn = connect()
    masterdata.init_schema(conn)
    conn.close()

    identities = {
        "maker": ({"username": "md.maker", "user_ref": "maker.user"}, {"MASTER_DATA"}),
        "checker": ({"username": "md.checker", "user_ref": "checker.user"}, {"MASTER_DATA_MANAGER"}),
        "dual": ({"username": "md.dual", "user_ref": "dual.user"}, {"MASTER_DATA_MANAGER"}),
        "reader": ({"username": "md.reader", "user_ref": "reader.user"}, {"READ_ONLY"}),
    }

    def authenticated_actor(conn, token):
        if token not in identities:
            raise HTTPException(401, {"code": "INVALID_TEST_SESSION"})
        return identities[token]

    monkeypatch.setattr(masterdata, "connect", connect)
    monkeypatch.setattr(masterdata, "authenticated_actor", authenticated_actor)
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC", "OFF")
    return connect


def _bulk(*rows):
    return masterdata.BulkRequest(
        domain="customer",
        rows=[
            masterdata.BulkRow(
                record_key=key,
                operation="CREATE",
                payload={"name": name},
                reason="Isolated acceptance test",
            )
            for key, name in rows
        ],
    )


def _change(key, operation, name, reason="Isolated acceptance test"):
    return masterdata.Change(
        domain="customer",
        record_key=key,
        operation=operation,
        payload={"name": name},
        reason=reason,
    )


def _error(exc, status, code):
    assert exc.value.status_code == status
    assert exc.value.detail["code"] == code


def test_bulk_preview_submission_checker_decision_versions_and_audit(uat_db):
    request = _bulk(("UAT-CUST-001", "UAT Customer One"),
                    ("UAT-CUST-002", "UAT Customer Two"))

    preview = masterdata.bulk_preview(request, x_m3_session="maker")
    assert (preview["valid"], preview["rejected"]) == (2, 0)
    db = uat_db()
    assert db.execute("SELECT COUNT(*) FROM md_records").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM md_change_requests").fetchone()[0] == 0
    db.close()

    submitted = masterdata.bulk_submit(request, x_m3_session="maker")
    assert (submitted["pending"], submitted["rejected"]) == (2, 0)
    first, second = [row["change_ref"] for row in submitted["rows"]]
    assert first != second

    db = uat_db()
    assert db.execute("SELECT COUNT(*) FROM md_records").fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM md_change_requests WHERE status='PENDING'"
    ).fetchone()[0] == 2
    db.close()

    with pytest.raises(HTTPException) as forbidden:
        masterdata.decide(first, masterdata.Decision(decision="APPROVE"),
                          x_m3_session="maker")
    _error(forbidden, 403, "CHECKER_PERMISSION_DENIED")

    approved = masterdata.decide(
        first, masterdata.Decision(decision="APPROVE", comment="UAT checker approval"),
        x_m3_session="checker",
    )
    assert approved == {"status": "APPROVED", "version": 1}

    rejected = masterdata.decide(
        second, masterdata.Decision(decision="REJECT", comment="UAT checker rejection"),
        x_m3_session="checker",
    )
    assert rejected["status"] == "REJECTED"

    versions = masterdata.versions(
        "customer", "UAT-CUST-001", x_m3_session="checker"
    )
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["change_ref"] == first

    audit = masterdata.audit_list(change_ref=first, x_m3_session="checker")
    assert {event["action"] for event in audit} == {
        "CHANGE_REQUEST_CREATE", "CHANGE_APPROVE_APPLY"
    }

    db = uat_db()
    assert db.execute(
        "SELECT COUNT(*) FROM md_records WHERE record_key='UAT-CUST-001'"
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM md_records WHERE record_key='UAT-CUST-002'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref=?", (second,)
    ).fetchone()[0] == "REJECTED"
    assert db.execute("SELECT COUNT(*) FROM md_versions").fetchone()[0] == 1
    db.close()


def test_maker_withdraw_checker_denial_and_self_approval(uat_db):
    with pytest.raises(HTTPException) as denied:
        masterdata.bulk_preview(
            _bulk(("UAT-CUST-003", "Denied Customer")), x_m3_session="reader"
        )
    _error(denied, 403, "MAKER_PERMISSION_DENIED")

    with pytest.raises(HTTPException) as denied_submit:
        masterdata.create_change(
            _change("UAT-CUST-003", "CREATE", "Denied Customer"),
            x_m3_session="reader",
        )
    _error(denied_submit, 403, "MAKER_PERMISSION_DENIED")

    submission = masterdata.create_change(
        _change("UAT-CUST-003", "CREATE", "Withdrawn Customer"),
        x_m3_session="maker",
    )
    ref = submission["change_ref"]

    with pytest.raises(HTTPException) as forbidden_withdraw:
        masterdata.withdraw_change(
            ref, masterdata.Withdrawal(reason="Not my change"),
            x_m3_session="checker",
        )
    _error(forbidden_withdraw, 403, "WITHDRAW_MAKER_ONLY")

    withdrawn = masterdata.withdraw_change(
        ref, masterdata.Withdrawal(reason="Maker withdrawal"),
        x_m3_session="maker",
    )
    assert withdrawn["status"] == "WITHDRAWN"

    with pytest.raises(HTTPException) as decided:
        masterdata.decide(
            ref, masterdata.Decision(decision="APPROVE"),
            x_m3_session="checker",
        )
    _error(decided, 409, "CHANGE_ALREADY_DECIDED")

    own = masterdata.create_change(
        _change("UAT-CUST-004", "CREATE", "Self Approval Denied"),
        x_m3_session="dual",
    )
    with pytest.raises(HTTPException) as self_approve:
        masterdata.decide(
            own["change_ref"], masterdata.Decision(decision="APPROVE"),
            x_m3_session="dual",
        )
    _error(self_approve, 409, "FOUR_EYES_VIOLATION")

    db = uat_db()
    assert db.execute("SELECT COUNT(*) FROM md_records").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM md_versions").fetchone()[0] == 0
    assert db.execute(
        "SELECT status FROM md_change_requests WHERE change_ref=?",
        (own["change_ref"],)
    ).fetchone()[0] == "PENDING"
    assert db.execute(
        "SELECT COUNT(*) FROM md_audit WHERE change_ref=? AND action='CHANGE_WITHDRAW'",
        (ref,),
    ).fetchone()[0] == 1
    db.close()


def test_version_two_audit_immutable_and_rejected_bulk_all_or_nothing(uat_db):
    first = masterdata.create_change(
        _change("UAT-CUST-005", "CREATE", "Version One"),
        x_m3_session="maker",
    )["change_ref"]
    masterdata.decide(
        first, masterdata.Decision(decision="APPROVE"),
        x_m3_session="checker",
    )

    update = masterdata.create_change(
        _change("UAT-CUST-005", "UPDATE", "Version Two"),
        x_m3_session="maker",
    )["change_ref"]
    result = masterdata.decide(
        update, masterdata.Decision(decision="APPROVE"),
        x_m3_session="checker",
    )
    assert result["version"] == 2

    history = masterdata.versions(
        "customer", "UAT-CUST-005", x_m3_session="checker"
    )
    assert [(x["version"], x["change_ref"]) for x in history] == [
        (2, update), (1, first)
    ]

    mixed = _bulk(("UAT-CUST-006", "New Valid Customer"),
                  ("uat-cust-005", "Case Collision"))
    preview = masterdata.bulk_preview(mixed, x_m3_session="maker")
    assert (preview["valid"], preview["rejected"]) == (1, 1)
    assert "RECORD_ALREADY_EXISTS" in preview["rows"][1]["errors"]
    with pytest.raises(HTTPException) as bulk_denied:
        masterdata.bulk_submit(mixed, x_m3_session="maker")
    _error(bulk_denied, 422, "BULK_PREVIEW_REJECTED")

    db = uat_db()
    assert db.execute(
        "SELECT COUNT(*) FROM md_change_requests WHERE record_key='UAT-CUST-006'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT version FROM md_records WHERE record_key='UAT-CUST-005'"
    ).fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM md_versions").fetchone()[0] == 2
    assert db.execute(
        "SELECT COUNT(*) FROM md_audit WHERE action='CHANGE_APPROVE_APPLY'"
    ).fetchone()[0] == 2
    with pytest.raises(sqlite3.DatabaseError, match="MASTER_AUDIT_IMMUTABLE"):
        db.execute("UPDATE md_audit SET action='TAMPER' WHERE id=1")
    with pytest.raises(sqlite3.DatabaseError, match="MASTER_AUDIT_IMMUTABLE"):
        db.execute("DELETE FROM md_audit WHERE id=1")
    db.close()
