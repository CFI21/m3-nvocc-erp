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
            return _Results([{"branch_code": "NLRTM"}])
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
    assert actor["branch"] == "NLRTM"
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
    assert result["branch"] == "NLRTM"


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


@pytest.mark.parametrize("role", ["AGENT", "DEPOT"])
def test_restricted_equipment_actor_requires_active_office(monkeypatch, role):
    class RevokedMembershipConn:
        def execute(self, sql, params):
            assert "iam_office_membership" in sql
            return _Results([])

    monkeypatch.setattr(
        equipment, "iam_session",
        lambda c, token: {"user_id": 7, "user_ref": "restricted-user",
                          "office_code": "RTM", "home_office_id": 1},
    )
    monkeypatch.setattr(equipment, "roles_for", lambda c, user: [{"role_code": role}])
    with pytest.raises(HTTPException) as exc:
        equipment.actor(
            RevokedMembershipConn(), "valid", "ADMIN",
            "FORGED-AGENT", "FORGED-BRANCH", "FORGED-DEPOT",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EQUIPMENT_ACTIVE_OFFICE_REQUIRED"


@pytest.mark.parametrize("iam_role", [" agent ", "Agent", "AGENT"])
def test_equipment_normalizes_authenticated_iam_role(monkeypatch, iam_role):
    class Conn:
        def execute(self, sql, params):
            if "iam_branches" in sql:
                return _Results([{"branch_code": "NLRTM"}])
            if "iam_party_access" in sql:
                return _Results([{"party_key": "A1"}])
            raise AssertionError("Unexpected SQL")
    from app import admin
    monkeypatch.setattr(equipment, "iam_session", lambda c, t: {
        "user_id": 7, "user_ref": "agent", "office_code": "RTM",
        "home_office_id": 1,
    })
    monkeypatch.setattr(equipment, "roles_for", lambda c, u: [{"role_code": iam_role}])
    monkeypatch.setattr(admin, "_assigned_agent_offices",
                        lambda c, u, a: [{"office_code": "RTM"}])
    result = equipment.actor(Conn(), "valid", "ADMIN", "OTHER", "OTHER", "OTHER")
    assert result["role"] == "AGENT"
    assert result["agent"] == "A1"


@pytest.mark.parametrize("roles", [[], [{"role_code": "UNKNOWN"}], [{"role_code": ""}]])
def test_equipment_missing_or_unknown_iam_roles_rejected(monkeypatch, roles):
    monkeypatch.setattr(
        equipment, "iam_session",
        lambda c, token: {"user_id": 7, "user_ref": "unknown-user",
                          "office_code": "RTM", "home_office_id": 1},
    )
    monkeypatch.setattr(equipment, "roles_for", lambda c, user: roles)
    with pytest.raises(HTTPException) as exc:
        equipment.actor(None, "valid", "SUPER_ADMIN", "A1", "NLRTM", "D1")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EQUIPMENT_IAM_ROLE_REQUIRED"


@pytest.mark.parametrize("user_ref", [None, "", "   "])
@pytest.mark.parametrize("iam_role", ["VIEWER", "ADMIN", "AGENT"])
def test_equipment_missing_iam_user_reference_denied(monkeypatch, user_ref, iam_role):
    # Identity validation happens before any scoped SQL query or party grant.
    monkeypatch.setattr(equipment, "iam_session", lambda _c, _t: {
        "user_id": 7, "user_ref": user_ref, "office_code": "RTM",
        "home_office_id": 1,
    })
    monkeypatch.setattr(
        equipment, "roles_for", lambda _c, _u: [{"role_code": iam_role}],
    )
    with pytest.raises(HTTPException) as exc:
        equipment.actor(None, "valid", "SUPER_ADMIN", "A1", "NLRTM", "D1")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EQUIPMENT_IAM_IDENTITY_REQUIRED"


@pytest.mark.parametrize("roles,expected", [
    (["VIEWER", "FINANCE"], "FINANCE"),
    (["OPS", "ADMIN"], "ADMIN"),
    (["DOCS", "AUDITOR"], "AUDITOR"),
    ([" agent ", "VIEWER"], "AGENT"),
])
def test_mrg_authenticated_roles_have_stable_precedence(monkeypatch, roles, expected):
    monkeypatch.setattr(mrg, "iam_session", lambda c, t: {
        "user_id": 7, "user_ref": "mrg-user",
    })
    monkeypatch.setattr(
        mrg, "iam_roles_for",
        lambda c, uid: [{"role_code": code} for code in roles],
    )
    result = mrg.actor(None, "valid", "SUPER_ADMIN")
    assert result["role"] == expected
    assert result["user"] == "mrg-user"


@pytest.mark.parametrize("roles,user_ref", [
    ([], "mrg-user"),
    ([""], "mrg-user"),
    (["ADMIN"], ""),
    (["FINANCE"], None),
])
def test_mrg_fails_closed_without_valid_iam_identity(monkeypatch, roles, user_ref):
    monkeypatch.setattr(mrg, "iam_session", lambda c, t: {
        "user_id": 7, "user_ref": user_ref,
    })
    monkeypatch.setattr(
        mrg, "iam_roles_for",
        lambda c, uid: [{"role_code": code} for code in roles],
    )
    with pytest.raises(HTTPException) as exc:
        mrg.actor(None, "valid", "ADMIN")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "MRG_IAM_IDENTITY_REQUIRED"


@pytest.mark.parametrize("roles", [["UNKNOWN"], ["FAKE_ADMIN", "CUSTOM_ROLE"]])
def test_mrg_rejects_unrecognized_authenticated_roles(monkeypatch, roles):
    monkeypatch.setattr(mrg, "iam_session", lambda c, t: {
        "user_id": 7, "user_ref": "mrg-user",
    })
    monkeypatch.setattr(
        mrg, "iam_roles_for",
        lambda c, u: [{"role_code": code} for code in roles],
    )
    with pytest.raises(HTTPException) as exc:
        mrg.actor(None, "valid", "ADMIN")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "MRG_IAM_ROLE_NOT_AUTHORIZED"


def test_mrg_filters_unrecognized_roles_from_authorization_set(monkeypatch):
    monkeypatch.setattr(mrg, "iam_session", lambda c, t: {
        "user_id": 7, "user_ref": "mrg-user",
    })
    monkeypatch.setattr(
        mrg, "iam_roles_for",
        lambda c, uid: [{"role_code": "FINANCE"}, {"role_code": "UNRECOGNIZED"}],
    )
    result = mrg.actor(None, "valid", "ADMIN")
    assert result["role"] == "FINANCE"
    assert result["roles"] == {"FINANCE"}
    assert "UNRECOGNIZED" not in result["roles"]


@pytest.mark.parametrize("iam_roles,expected", [
    (["FINANCE", "UNRECOGNIZED"], "FINANCE"),
    (["VIEWER", "UNRECOGNIZED"], "VIEWER"),
    (["OPS", "FINANCE", "UNRECOGNIZED"], "FINANCE"),
])
def test_mrg_audit_role_from_effective_permissions(monkeypatch, iam_roles, expected):
    monkeypatch.setattr(mrg, "iam_session", lambda c, token: {
        "user_id": 7, "user_ref": "mrg-user",
    })
    monkeypatch.setattr(mrg, "iam_roles_for", lambda c, user: [
        {"role_code": role} for role in iam_roles
    ])
    result = mrg.actor(None, "valid", "ADMIN")
    assert result["role"] == expected
    assert result["roles"] <= (mrg.MAKER | mrg.CHECKER | mrg.VIEW)


@pytest.mark.parametrize("session", [None, {}, {"user_id": None}, {"user_id": 0}])
def test_mrg_rejects_invalid_iam_session_before_role_lookup(monkeypatch, session):
    monkeypatch.setattr(mrg, "iam_session", lambda _c, _t: session)
    monkeypatch.setattr(
        mrg, "iam_roles_for",
        lambda _c, _uid: pytest.fail("Role lookup must not run for invalid session"),
    )
    with pytest.raises(HTTPException) as exc:
        mrg.actor(None, "valid", "SUPER_ADMIN")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "MRG_IAM_IDENTITY_REQUIRED"


@pytest.mark.parametrize("roles", [None, [], ()])
def test_mrg_rejects_missing_iam_role_collection(monkeypatch, roles):
    monkeypatch.setattr(mrg, "iam_session", lambda _c, _t: {
        "user_id": 7, "user_ref": "mrg-user",
    })
    monkeypatch.setattr(mrg, "iam_roles_for", lambda _c, _u: roles)
    with pytest.raises(HTTPException) as exc:
        mrg.actor(None, "valid", "ADMIN")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "MRG_IAM_ROLE_NOT_AUTHORIZED"


@pytest.mark.parametrize("rows", [
    [None], ["bad-row"], [{}], [{"wrong_key": "ADMIN"}],
    [{"role_code": "FINANCE"}, None],
])
def test_mrg_malformed_role_rows_fail_closed_or_ignore_invalid(monkeypatch, rows):
    monkeypatch.setattr(mrg, "iam_session", lambda c, t: {
        "user_id": 7, "user_ref": "mrg-user",
    })
    monkeypatch.setattr(mrg, "iam_roles_for", lambda c, uid: rows)
    if rows[0] == {"role_code": "FINANCE"}:
        result = mrg.actor(None, "valid", "ADMIN")
        assert result["roles"] == {"FINANCE"}
    else:
        with pytest.raises(HTTPException) as exc:
            mrg.actor(None, "valid", "ADMIN")
        assert exc.value.status_code == 403


def test_mrg_accepts_real_sqlite_row_iam_roles(monkeypatch):
    import sqlite3
    db=sqlite3.connect(":memory:")
    db.row_factory=sqlite3.Row
    db.execute("CREATE TABLE iam_role_test (role_code TEXT)")
    db.execute("INSERT INTO iam_role_test VALUES ('FINANCE')")
    rows=db.execute("SELECT role_code FROM iam_role_test").fetchall()
    monkeypatch.setattr(mrg, "iam_session", lambda _c, _t: {
        "user_id": 7, "user_ref": "mrg-user",
    })
    monkeypatch.setattr(mrg, "iam_roles_for", lambda _c, _uid: rows)
    result=mrg.actor(None, "valid", "VIEWER")
    assert result["role"] == "FINANCE"
    assert result["roles"] == {"FINANCE"}
    db.close()


def test_mrg_sqlite_session_row(monkeypatch):
    import sqlite3
    db=sqlite3.connect(":memory:")
    db.row_factory=sqlite3.Row
    db.execute("CREATE TABLE t(user_id INTEGER, user_ref TEXT)")
    db.execute("INSERT INTO t VALUES (7, 'tester')")
    session_row=db.execute("SELECT * FROM t").fetchone()
    monkeypatch.setattr(mrg, "iam_session", lambda c, token: session_row)
    monkeypatch.setattr(mrg, "iam_roles_for", lambda c, user: [{"role_code": "FINANCE"}])
    result=mrg.actor(None, "valid", "VIEWER")
    assert result["user"] == "tester"
    assert result["role"] == "FINANCE"
    db.close()


def test_equipment_sqlite_session_row(monkeypatch):
    import sqlite3
    from app import admin
    db=sqlite3.connect(":memory:")
    db.row_factory=sqlite3.Row
    db.execute("CREATE TABLE t(user_id INTEGER, user_ref TEXT, office_code TEXT, home_office_id INTEGER)")
    db.execute("INSERT INTO t VALUES (7, 'tester', 'RTM', 1)")
    row=db.execute("SELECT * FROM t").fetchone()
    monkeypatch.setattr(equipment, "iam_session", lambda c, token: row)
    monkeypatch.setattr(equipment, "roles_for", lambda c, user: [{"role_code": "AGENT"}])
    monkeypatch.setattr(admin, "_assigned_agent_offices", lambda c, user, agent: [{"office_code": "RTM"}])
    result=equipment.actor(_GrantConn(["REAL-AGENT"]), "valid", "ADMIN",
                           "FAKE-AGENT", "FAKE-BRANCH", "FAKE-DEPOT")
    assert result["user"] == "tester"
    assert result["role"] == "AGENT"
    assert result["agent"] == "REAL-AGENT"
    assert result["branch"] == "NLRTM"
    db.close()


@pytest.mark.parametrize("bad_session", [None, {}, {"user_id": None}, {"user_id": 0}])
def test_equipment_rejects_invalid_iam_session_before_role_query(monkeypatch, bad_session):
    monkeypatch.setattr(equipment, "iam_session", lambda c, token: bad_session)
    monkeypatch.setattr(equipment, "roles_for",
                        lambda c, user: pytest.fail("invalid session queried roles"))
    with pytest.raises(HTTPException) as exc:
        equipment.actor(None, "valid", "ADMIN", "FAKE", "FAKE", "FAKE")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EQUIPMENT_IAM_IDENTITY_REQUIRED"


@pytest.mark.parametrize("bad_roles", [None, [], [None], ["corrupt"], [{}]])
def test_equipment_rejects_invalid_iam_roles(monkeypatch, bad_roles):
    monkeypatch.setattr(equipment, "iam_session", lambda c, token: {
        "user_id": 7, "user_ref": "tester", "home_office_id": 1,
        "office_code": "RTM",
    })
    monkeypatch.setattr(equipment, "roles_for", lambda c, user: bad_roles)
    with pytest.raises(HTTPException) as exc:
        equipment.actor(None, "valid", "ADMIN", "FAKE", "FAKE", "FAKE")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EQUIPMENT_IAM_ROLE_REQUIRED"


@pytest.mark.parametrize("invalid_rows", [[None], [{}], ["invalid"], [{"wrong_key": "FINANCE"}]])
def test_mrg_invalid_iam_role_rows_return_role_denial(monkeypatch, invalid_rows):
    monkeypatch.setattr(mrg, "iam_session", lambda _c, _t: {
        "user_id": 7, "user_ref": "valid-user",
    })
    monkeypatch.setattr(mrg, "iam_roles_for", lambda _c, _u: invalid_rows)
    with pytest.raises(HTTPException) as exc:
        mrg.actor(None, "valid", "ADMIN")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "MRG_IAM_ROLE_NOT_AUTHORIZED"
