"""Fail-closed Booking action contract tests; no database or external services."""
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.main import ActionBody, action_record


@pytest.mark.parametrize("action", ["submit-rfa", "approve"])
def test_booking_approval_actions_require_governed_iam(action):
    with patch("app.main.bulk_permission_enabled", return_value=False):
        with pytest.raises(HTTPException) as exc:
            action_record("booking", 1, action, ActionBody(version=1))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "BOOKING_APPROVAL_FLOW_REQUIRES_IAM"


@pytest.mark.parametrize("action", ["release", "reissue", "reverse", "advance", "reject"])
def test_booking_rejects_unsupported_mutations_before_database_access(action):
    with pytest.raises(HTTPException) as exc:
        action_record("booking", 1, action, ActionBody(version=1))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "BOOKING_ACTION_NOT_SUPPORTED"


def test_rfa_action_is_booking_only():
    with pytest.raises(HTTPException) as exc:
        action_record("bl", 1, "submit-rfa", ActionBody(version=1))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "BOOKING_RFA_ACTION_ONLY"
