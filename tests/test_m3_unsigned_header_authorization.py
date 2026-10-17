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


@pytest.mark.parametrize("alias", ["unknown_alias", "unexpected", "", "legacy"])
def test_equipment_rejects_untrusted_sql_scope_alias(alias):
    with pytest.raises(HTTPException) as exc:
        equipment.scope_clause({"role": "AGENT", "agent": "A1"}, alias=alias)
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "EQUIPMENT_INVALID_SQL_ALIAS"


@pytest.mark.parametrize("context", [{}, {"role": None}, {"role": ""}])
def test_equipment_missing_role_is_denied(context):
    with pytest.raises(HTTPException) as exc:
        equipment.scope_clause(context)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EQUIPMENT_ROLE_SCOPE_DENIED"


@pytest.mark.parametrize("role,code", [
    ("AGENT", "AGENT_SCOPE_REQUIRED"),
    ("DEPOT", "DEPOT_SCOPE_REQUIRED"),
])
def test_equipment_missing_party_scope_denied_with_403(role, code):
    with pytest.raises(HTTPException) as exc:
        equipment.scope_clause({"role": role})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == code


@pytest.mark.parametrize("role,key,value", [
    ("AGENT", "agent", " A1 "),
    ("DEPOT", "depot", " D1 "),
    ("OPS", "branch", " NLRTM "),
])
def test_equipment_scope_parameters_normalized(role, key, value):
    clause, params = equipment.scope_clause({"role": role, key: value})
    assert clause.endswith("=?")
    assert params == [value.strip()]


@pytest.mark.parametrize("role,key,code", [
    ("AGENT", "agent", "AGENT_SCOPE_REQUIRED"),
    ("DEPOT", "depot", "DEPOT_SCOPE_REQUIRED"),
    ("OPS", "branch", "EQUIPMENT_BRANCH_SCOPE_REQUIRED"),
    ("BRANCH_OPS", "branch", "EQUIPMENT_BRANCH_SCOPE_REQUIRED"),
    ("DOCS", "branch", "EQUIPMENT_BRANCH_SCOPE_REQUIRED"),
    ("EQUIPMENT_CONTROLLER", "branch", "EQUIPMENT_BRANCH_SCOPE_REQUIRED"),
])
def test_equipment_whitespace_only_scope_fails_closed(role, key, code):
    with pytest.raises(HTTPException) as exc:
        equipment.scope_clause({"role": role, key: "   "})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == code


@pytest.mark.parametrize("role,key,value,code", [
    ("AGENT", "agent", 123, "AGENT_SCOPE_REQUIRED"),
    ("DEPOT", "depot", ["D1"], "DEPOT_SCOPE_REQUIRED"),
    ("OPS", "branch", {"id": "NLRTM"}, "EQUIPMENT_BRANCH_SCOPE_REQUIRED"),
])
def test_equipment_rejects_non_string_scope_values(role, key, value, code):
    with pytest.raises(HTTPException) as exc:
        equipment.scope_clause({"role": role, key: value})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == code


@pytest.mark.parametrize("role,grant", [
    ("AGENT", "  "),
    ("AGENT", 123),
    ("DEPOT", "  "),
    ("DEPOT", ["D1"]),
])
def test_equipment_rejects_malformed_iam_party_grants(monkeypatch, role, grant):
    monkeypatch.setattr(
        equipment, "iam_session",
        lambda c, token: {"user_id": 7, "user_ref": "uat-user",
                          "office_code": "RTM", "home_office_id": 1},
    )
    monkeypatch.setattr(
        equipment, "roles_for",
        lambda c, user: [{"role_code": role}],
    )
    with pytest.raises(HTTPException) as exc:
        equipment.actor(
            _GrantConn([grant]), "valid", "ADMIN",
            "SPOOFED-AGENT", "SPOOFED-BRANCH", "SPOOFED-DEPOT",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EQUIPMENT_TRUSTED_SCOPE_REQUIRED"


@pytest.mark.parametrize("role", ["OPS", "BRANCH_OPS", "DOCS", "EQUIPMENT_CONTROLLER"])
def test_equipment_actor_requires_authoritative_branch(monkeypatch, role):
    monkeypatch.setattr(
        equipment, "iam_session",
        lambda c, token: {"user_id": 7, "user_ref": "ops-user",
                          "office_code": "RTM", "home_office_id": 1},
    )
    monkeypatch.setattr(equipment, "roles_for", lambda c, user: [{"role_code": role}])
    with pytest.raises(HTTPException) as exc:
        equipment.actor(
            _GrantConn([]), "valid", "ADMIN", "AGENT-OVERRIDE",
            "BRANCH-OVERRIDE", "DEPOT-OVERRIDE",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EQUIPMENT_BRANCH_SCOPE_REQUIRED"


def test_equipment_branch_requires_active_iam_office_membership(monkeypatch):
    """A stale or revoked home office cannot authorize branch equipment access."""
    class MembershipConn:
        def execute(self, sql, params):
            assert "iam_office_membership" in sql
            assert "m.status='ACTIVE'" in sql
            assert "o.status='ACTIVE'" in sql
            assert params == (7, 1)
            return _Results([])

    monkeypatch.setattr(
        equipment, "iam_session",
        lambda c, token: {"user_id": 7, "user_ref": "ops-user",
                          "office_code": "RTM", "home_office_id": 1},
    )
    monkeypatch.setattr(equipment, "roles_for", lambda c, user: [{"role_code": "OPS"}])
    with pytest.raises(HTTPException) as exc:
        equipment.actor(MembershipConn(), "valid", "ADMIN", "A1", "RTM", "D1")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EQUIPMENT_BRANCH_SCOPE_REQUIRED"
