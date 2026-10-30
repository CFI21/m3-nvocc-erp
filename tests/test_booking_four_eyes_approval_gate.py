"""Isolated Booking approval negative-path tests; all SQL calls are mocked."""
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.main import ActionBody, action_record


@pytest.mark.parametrize(
    "actor_role,maker,checker,expected_status,expected_code",
    [
        ("MASTER_DATA", "maker-1", "checker-1", 403, "BOOKING_INDEPENDENT_APPROVER_REQUIRED"),
        ("SUPER_ADMIN", "", "checker-1", 409, "BOOKING_RFA_MAKER_IDENTITY_REQUIRED"),
        ("SUPER_ADMIN", "maker-1", "maker-1", 403, "BOOKING_FOUR_EYES_VIOLATION"),
        ("SUPER_ADMIN", "maker-1", "", 403, "BOOKING_FOUR_EYES_VIOLATION"),
    ],
)
def test_booking_approval_rejects_unauthorized_or_nonindependent_checker(
    actor_role, maker, checker, expected_status, expected_code
):
    conn = MagicMock()
    record = {
        "id": 99,
        "job_id": 1,
        "job_ref": "M3-UAT-BKG-50001",
        "version": 2,
        "status": "RFA",
        "payload_json": json.dumps({"RFA Submitted By": maker}),
    }
    conn.execute.return_value.fetchone.return_value = record
    actor = {"role": actor_role, "session": {"user_ref": checker}}
    with (
        patch("app.main.bulk_permission_enabled", return_value=True),
        patch("app.main.connect", return_value=conn),
        patch("app.main.tx"),
        patch("app.main.module_where", return_value=("t.module=?", ["booking"])),
        patch("app.main.governed_job_actor", return_value=actor),
        patch("app.main.serialize_tx", return_value={}),
        patch("app.main.job_context", return_value={"closed": False}),
    ):
        with pytest.raises(HTTPException) as exc:
            action_record("booking", 99, "approve", ActionBody(version=2))
    assert exc.value.status_code == expected_status
    assert exc.value.detail["code"] == expected_code
    conn.close.assert_called_once()


def test_booking_approval_rejects_stale_version_before_mutation():
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = {
        "id": 99, "job_id": 1, "job_ref": "M3-UAT-BKG-50001",
        "version": 4, "status": "RFA", "payload_json": "{}",
    }
    with (
        patch("app.main.bulk_permission_enabled", return_value=True),
        patch("app.main.connect", return_value=conn),
        patch("app.main.tx"),
        patch("app.main.module_where", return_value=("t.module=?", ["booking"])),
        patch("app.main.governed_job_actor", return_value={"role": "SUPER_ADMIN", "session": {"user_ref": "checker-1"}}),
        patch("app.main.serialize_tx", return_value={}),
    ):
        with pytest.raises(HTTPException) as exc:
            action_record("booking", 99, "approve", ActionBody(version=3))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "OPTIMISTIC_LOCK_CONFLICT"
    assert exc.value.detail["current_version"] == 4
    conn.close.assert_called_once()
