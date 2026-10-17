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


class _GrantConn:
    def __init__(self, grants):
        self.grants = grants

    def execute(self, sql, params):
        if "iam_branches" in sql:
            return _Results([])
        if "iam_party_access" in sql:
            return _Results([{"party_key": key} for key in self.grants])
        raise AssertionError("Unexpected IAM SQL")


class _Results:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


@pytest.mark.parametrize("grants", [[], ["AGENT-A", "AGENT-B"], [""]])
def test_equipment_agent_requires_unambiguous_trusted_grant(monkeypatch, grants):
    monkeypatch.setattr(
        equipment, "iam_session",
        lambda c, token: {"user_id": 7, "user_ref": "agent-user",
                          "office_code": "RTM", "home_office_id": 1},
    )
    monkeypatch.setattr(equipment, "roles_for", lambda c, user: [{"role_code": "AGENT"}])
    with pytest.raises(HTTPException) as exc:
        equipment.actor(
            _GrantConn(grants), "valid", "ADMIN", "SPOOFED",
            "SPOOFED-BRANCH", "SPOOFED-DEPOT",
        )
    assert exc.value.status_code == 403


def test_equipment_agent_scope_uses_iam_not_headers(monkeypatch):
    from app import admin
    monkeypatch.setattr(
        equipment, "iam_session",
        lambda c, token: {"user_id": 7, "user_ref": "agent-user",
                          "office_code": "RTM", "home_office_id": 1},
    )
    monkeypatch.setattr(equipment, "roles_for", lambda c, user: [{"role_code": "AGENT"}])
    monkeypatch.setattr(
        admin, "_assigned_agent_offices",
        lambda conn, user, agent: [{"office_code": "RTM"}],
    )
    actor = equipment.actor(
        _GrantConn(["REAL-AGENT"]), "valid", "ADMIN",
        "SPOOFED-AGENT", "SPOOFED-BRANCH", "SPOOFED-DEPOT",
    )
    assert actor["role"] == "AGENT"
    assert actor["agent"] == "REAL-AGENT"
    assert actor["branch"] is None
    assert actor["depot"] is None


@pytest.mark.parametrize("grants", [[], ["DEPOT-1", "DEPOT-2"], [""]])
def test_equipment_depot_requires_unique_iam_grant(monkeypatch, grants):
    monkeypatch.setattr(
        equipment, "iam_session",
        lambda c, token: {"user_id": 7, "user_ref": "depot-user",
                          "office_code": "RTM", "home_office_id": 1},
    )
    monkeypatch.setattr(equipment, "roles_for", lambda c, user: [{"role_code": "DEPOT"}])
    with pytest.raises(HTTPException) as exc:
        equipment.actor(
            _GrantConn(grants), "valid", "ADMIN",
            "FAKE-AGENT", "FAKE-BRANCH", "FAKE-DEPOT",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] in {
        "EQUIPMENT_AMBIGUOUS_DEPOT_GRANTS", "EQUIPMENT_TRUSTED_SCOPE_REQUIRED"
    }


def test_equipment_depot_scope_from_iam_not_request_headers(monkeypatch):
    monkeypatch.setattr(
        equipment, "iam_session",
        lambda c, token: {"user_id": 7, "user_ref": "depot-user",
                          "office_code": "RTM", "home_office_id": 1},
    )
    monkeypatch.setattr(equipment, "roles_for", lambda c, user: [{"role_code": "DEPOT"}])
    result = equipment.actor(
        _GrantConn(["REAL-DEPOT"]), "valid", "ADMIN",
        "FAKE-AGENT", "FAKE-BRANCH", "FAKE-DEPOT",
    )
    assert result["role"] == "DEPOT"
    assert result["depot"] == "REAL-DEPOT"
    assert result["agent"] is None
    assert result["branch"] is None


@pytest.mark.parametrize("role", ["OPS", "BRANCH_OPS", "DOCS", "EQUIPMENT_CONTROLLER"])
def test_equipment_branch_role_requires_trusted_branch(role):
    with pytest.raises(HTTPException) as exc:
        equipment.scope_clause({"role": role, "branch": None})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EQUIPMENT_BRANCH_SCOPE_REQUIRED"


@pytest.mark.parametrize("role", ["OPS", "BRANCH_OPS", "DOCS", "EQUIPMENT_CONTROLLER"])
def test_equipment_branch_role_filters_exact_branch(role):
    clause, params = equipment.scope_clause({"role": role, "branch": "NLRTM"})
    assert clause == " AND c.branch_code=?"
    assert params == ["NLRTM"]
    assert "IS NULL" not in clause


def test_equipment_unknown_role_denied_even_for_read():
    with pytest.raises(HTTPException) as exc:
        equipment.scope_clause({"role": "UNRECOGNIZED", "branch": "NLRTM"})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EQUIPMENT_ROLE_SCOPE_DENIED"
