"""HTTP-level Checker APPROVE acceptance for atomic bulk, disposable SQLite only.

No Render, Neon, ANCLINE, production credentials, or external side effects.
"""
from test_m3_atomic_bulk_http_real_iam_uat import sandbox_client, auth, payload
from app import db


def test_atomic_bulk_independent_checker_approval_versions_and_audit(sandbox_client):
    client = sandbox_client
    maker = auth(client, "md.maker", "Maker123!")
    checker = auth(client, "md.checker", "Checker123!")
    rows = payload("M3-APPROVAL-A", "M3-APPROVAL-B")
    submitted = client.post(
        "/api/masterdata/bulk/submit-atomic",
        headers={**maker, "Idempotency-Key": "fixture-http-approval-001"},
        json=rows,
    )
    assert submitted.status_code == 200, submitted.text
    changes = submitted.json()["rows"]
    assert submitted.json()["pending"] == 2
    assert len(changes) == 2

    for change in changes:
        ref = change["change_ref"]
        # Maker cannot approve; only a separate valid Checker can.
        denied = client.post(
            f"/api/masterdata/changes/{ref}/decision",
            headers=maker, json={"decision": "APPROVE", "comment": "forbidden maker approval"},
        )
        assert denied.status_code == 403, denied.text

        accepted = client.post(
            f"/api/masterdata/changes/{ref}/decision",
            headers=checker, json={"decision": "APPROVE", "comment": "disposable HTTP validation"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "APPROVED"
        assert accepted.json()["version"] == 1

        # A second decision must never mutate the approved record.
        duplicate = client.post(
            f"/api/masterdata/changes/{ref}/decision",
            headers=checker, json={"decision": "APPROVE"},
        )
        assert duplicate.status_code == 409, duplicate.text

        record_key = change["record_key"]
        versions = client.get(
            f"/api/masterdata/versions/agent/{record_key}", headers=checker)
        assert versions.status_code == 200, versions.text
        assert any(v["change_ref"] == ref and v["version"] == 1
                   for v in versions.json())

        audit = client.get(
            "/api/masterdata/audit", headers=checker, params={"change_ref": ref})
        assert audit.status_code == 200, audit.text
        actions = {event["action"] for event in audit.json()}
        assert {"CHANGE_REQUEST_CREATE", "CHANGE_APPROVE_APPLY"} <= actions

    conn = db.connect()
    try:
        approved = conn.execute(
            "SELECT record_key, approved_by, version FROM md_records "
            "WHERE domain='agent' AND record_key LIKE 'M3-APPROVAL-%'").fetchall()
        assert len(approved) == 2
        assert all(row["approved_by"] != "USR-005" and row["version"] == 1
                   for row in approved)
        assert conn.execute(
            "SELECT COUNT(*) FROM md_change_requests "
            "WHERE status='PENDING' AND record_key LIKE 'M3-APPROVAL-%'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM md_bulk_idempotency").fetchone()[0] == 1
    finally:
        conn.close()

    for session in (maker, checker):
        assert client.post("/api/admin/auth/logout", headers=session).status_code == 200

