import pytest
from fastapi import HTTPException

from app.screen_catalog import build_catalog
from app.screen_integration import quick_actions, action_route

MUTATIONS={
    "create","edit","copy","approve","release","hold","cancel","amend","reissue",
    "reverse","retry","activate","deactivate","change-request","reject","advance"
}
READ_ONLY_ROLES={"AUDITOR","VIEWER"}


def test_all_196_authoritative_screens_have_governed_mutation_surface():
    catalog=build_catalog()
    assert catalog["screen_count"]==196
    assert len({s["screen_id"] for s in catalog["screens"]})==196
    for s in catalog["screens"]:
        for role in ("SUPER_ADMIN","ADMIN","OPS","DOCS","FINANCE","GL_MANAGER","GL_ACCOUNTANT","TREASURY_MANAGER","TREASURY","AGENT","AUDITOR","VIEWER"):
            try:
                qa=quick_actions(s["screen_id"],role)
            except HTTPException as exc:
                assert exc.status_code==403
                continue
            for action in set(qa["visible_actions"]) & MUTATIONS:
                route=action_route(s["screen_id"],action,1,1,role=role)
                assert route["mode"] in {"EXISTING_API","EXISTING_GOVERNANCE","MASTER_DATA_GOVERNANCE","EXISTING_SCREEN_WORKFLOW"}


def test_read_only_roles_never_receive_mutation_actions():
    for s in build_catalog()["screens"]:
        for role in READ_ONLY_ROLES:
            try:
                visible=set(quick_actions(s["screen_id"],role)["visible_actions"])
            except HTTPException as exc:
                assert exc.status_code==403
                continue
            assert not (visible & MUTATIONS), (s["screen_id"],role,visible & MUTATIONS)


def test_direct_action_route_cannot_bypass_role_permissions():
    checks=[
        ("agent-tasks::booking","approve","AGENT"),
        ("agent-tasks::booking","release","AGENT"),
        ("gl-accounts::voucher","approve","AGENT"),
        ("treasury::cashbook","release","VIEWER"),
        ("master-data::approval-queue","approve","VIEWER"),
    ]
    for screen_id,action,role in checks:
        with pytest.raises(HTTPException) as exc:
            action_route(screen_id,action,1,1,role=role)
        assert exc.value.status_code==403


def test_master_data_mutations_route_only_to_existing_governance():
    for s in build_catalog()["screens"]:
        if s["domain"]!="Master Data":
            continue
        for action in ("change-request","approve","reject","activate","deactivate","version-history"):
            try:
                visible=set(quick_actions(s["screen_id"],"ADMIN")["visible_actions"])
            except HTTPException:
                continue
            if action not in visible:
                continue
            route=action_route(s["screen_id"],action,1,1,role="ADMIN")
            assert route["mode"] in {"EXISTING_GOVERNANCE","MASTER_DATA_GOVERNANCE","EXISTING_SCREEN_WORKFLOW"}


def test_financial_mutation_routes_do_not_create_parallel_api():
    for s in build_catalog()["screens"]:
        if s["screen_id"].startswith("gl-accounts::"):
            for action in set(quick_actions(s["screen_id"],"ADMIN")["visible_actions"]) & MUTATIONS:
                route=action_route(s["screen_id"],action,1,1,role="ADMIN")
                if route["mode"]=="EXISTING_API":
                    assert route["path"].startswith("/api/v1/gl/")
        if s["domain"]=="Treasury / AR-AP":
            for action in set(quick_actions(s["screen_id"],"ADMIN")["visible_actions"]) & MUTATIONS:
                route=action_route(s["screen_id"],action,1,1,role="ADMIN")
                if route["mode"]=="EXISTING_API":
                    assert route["path"].startswith("/api/v1/treasury/")
