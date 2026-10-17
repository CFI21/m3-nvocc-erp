"""Security regression: unsigned role headers must never mint privileged API actors."""
import pytest
from fastapi import HTTPException
from app import mrg, clx070_container_master_control as equipment


@pytest.mark.parametrize("header_role", [
    None, "VIEWER", "AGENT", "FINANCE", "ADMIN", "SUPER_ADMIN",
])
def test_mrg_rejects_unsigned_header_role(header_role):
    with pytest.raises(HTTPException) as exc:
        mrg.actor(None, None, header_role)
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "MRG_SESSION_REQUIRED"


@pytest.mark.parametrize("header_role", [
    None, "VIEWER", "AGENT", "DEPOT", "FINANCE", "ADMIN", "SUPER_ADMIN",
])
def test_equipment_rejects_unsigned_header_role_and_scope(header_role):
    with pytest.raises(HTTPException) as exc:
        equipment.actor(None, None, header_role, "ANY-AGENT", "ANY-BRANCH", "ANY-DEPOT")
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "EQUIPMENT_SESSION_REQUIRED"
