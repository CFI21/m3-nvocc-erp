"""Ensure caller-supplied office headers cannot extend IAM screen access."""
from app import screen_integration


def test_untrusted_office_header_does_not_grant_office(monkeypatch):
    trusted = {"role": "OPS", "branch": "NLRTM", "office": None, "agent": None, "depot": None}
    monkeypatch.setattr(
        screen_integration, "scope_actor",
        lambda conn, token, role, agent, branch, depot: dict(trusted),
    )
    actual = screen_integration._read_actor(
        None, "ADMIN", "validated-token", None, None, None, "SPOOFED-OFFICE"
    )
    assert actual["office"] is None
    assert actual["role"] == "OPS"


def test_trusted_iam_office_survives_spoofed_header(monkeypatch):
    trusted = {"role": "OPS", "branch": "NLRTM", "office": "RTM", "agent": None, "depot": None}
    monkeypatch.setattr(
        screen_integration, "scope_actor",
        lambda conn, token, role, agent, branch, depot: dict(trusted),
    )
    actual = screen_integration._read_actor(
        None, "SUPER_ADMIN", "validated-token", None, None, None, "FAKE"
    )
    assert actual["office"] == "RTM"
    assert actual["role"] == "OPS"


def test_missing_iam_role_cannot_inherit_caller_role(monkeypatch):
    import pytest
    from fastapi import HTTPException
    monkeypatch.setattr(
        screen_integration, "scope_actor",
        lambda conn, token, role, agent, branch, depot: {"role": None, "office": None},
    )
    with pytest.raises(HTTPException) as denied:
        screen_integration._read_actor(
            None, "SUPER_ADMIN", "validated-token", None, None, None, "SPOOFED"
        )
    assert denied.value.status_code == 403
    assert denied.value.detail["code"] == "SCREEN_IAM_ROLE_REQUIRED"
