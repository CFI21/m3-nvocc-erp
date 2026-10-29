"""Fail-closed checks for the governed job-access HTTP handler.

These are isolated unit tests; they do not substitute for live authenticated UAT.
"""
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from app import admin


class FakeConnection:
    def close(self):
        pass


def test_bulk_access_disabled_returns_404_without_database_access():
    with patch("app.bulk_permission_governance.enabled", return_value=False), patch.object(admin, "connect") as connect:
        with pytest.raises(HTTPException) as exc:
            admin.bulk_job_access(action="approve", x_m3_session=None)
        assert exc.value.status_code == 404
        connect.assert_not_called()


def test_bulk_access_missing_session_fails_closed():
    conn = FakeConnection()
    with patch("app.bulk_permission_governance.enabled", return_value=True), patch.object(admin, "connect", return_value=conn), patch.object(admin, "session", side_effect=HTTPException(401, {"code":"SESSION_REQUIRED"})):
        with pytest.raises(HTTPException) as exc:
            admin.bulk_job_access(action="approve", x_m3_session=None)
        assert exc.value.status_code == 401


def test_bulk_access_requires_identity_permission():
    conn = FakeConnection()
    user = {"user_id": 1, "office_code": "RTM"}
    with patch("app.bulk_permission_governance.enabled", return_value=True), patch.object(admin, "connect", return_value=conn), patch.object(admin, "session", return_value=user), patch.object(admin, "permission", return_value=False), patch("app.bulk_permission_governance.bulk_validate") as bulk:
        with pytest.raises(HTTPException) as exc:
            admin.bulk_job_access(action="approve", x_m3_session="test-session")
        assert exc.value.status_code == 403
        bulk.assert_not_called()


def test_bulk_access_returns_governed_validator_only_for_authorized_session():
    conn = FakeConnection()
    user = {"user_id": 1, "office_code": "RTM"}
    expected = {"tests": 25, "authorized": 8, "blocked": 17}
    with patch("app.bulk_permission_governance.enabled", return_value=True), patch.object(admin, "connect", return_value=conn), patch.object(admin, "session", return_value=user), patch.object(admin, "permission", return_value=True), patch("app.bulk_permission_governance.bulk_validate", return_value=expected) as bulk:
        result = admin.bulk_job_access(action="approve", x_m3_session="test-session")
        assert result == expected
        bulk.assert_called_once_with(conn, "agent-tasks", "approve")
