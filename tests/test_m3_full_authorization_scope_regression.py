import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.screen_catalog import build_catalog
from app.screen_integration import (
    action_route, events, navigation_event, simulate_email, EmailIntent,
    screen_data, related, workflow, search,
)
from app.main import audit_list, exception_list


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "full_auth_scope.db")
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH


def job_scope(job_ref="50001"):
    c=db.connect()
    try:
        r=c.execute("""SELECT j.job_ref,j.office_code,j.branch_code,a.code agent_code,c.code customer_code
                       FROM jobs j JOIN agents a ON a.id=j.agent_id JOIN customers c ON c.id=j.customer_id
                       WHERE j.job_ref=?""",(job_ref,)).fetchone()
        return dict(r)
    finally:c.close()


def test_frozen_196_screen_surface():
    c=build_catalog()
    assert c["screen_count"]==196
    assert len({s["screen_id"] for s in c["screens"]})==196
    assert len({s["route"] for s in c["screens"]})==196


def test_action_route_cannot_bypass_screen_role():
    with pytest.raises(HTTPException) as exc:
        action_route("gl-accounts::invoice","approve",1,1,role="AGENT")
    assert exc.value.status_code==403
    ok=action_route("agent-tasks::booking","quick-view",1,1,role="AGENT")
    assert ok["mode"]=="CLIENT_OR_CLX011"


def test_clx011_events_are_agent_scoped(isolated):
    s=job_scope()
    navigation_event("agent-tasks::booking",job_ref="50001",x_role="AGENT",x_agent_scope=s["agent_code"])
    rows=events(100,x_role="AGENT",x_agent_scope=s["agent_code"])
    assert rows
    assert all(r["job_ref"]=="50001" for r in rows)
    wrong=events(100,x_role="AGENT",x_agent_scope="WRONG-AGENT")
    assert wrong==[]


def test_navigation_event_rejects_cross_scope_job(isolated):
    with pytest.raises(HTTPException) as exc:
        navigation_event("agent-tasks::booking",job_ref="50001",x_role="AGENT",x_agent_scope="WRONG-AGENT")
    assert exc.value.status_code==404


def test_simulated_email_rejects_cross_scope_job(isolated):
    body=EmailIntent(screen_id="agent-tasks::booking",job_ref="50001",subject="scope test")
    with pytest.raises(HTTPException) as exc:
        simulate_email(body,x_role="AGENT",x_agent_scope="WRONG-AGENT")
    assert exc.value.status_code==404
    s=job_scope()
    out=simulate_email(body,x_role="AGENT",x_agent_scope=s["agent_code"])
    assert out["delivery"]=="SIMULATED_ONLY"


def test_customer_office_branch_scope_consistent_on_read_paths(isolated):
    s=job_scope()
    kwargs=dict(x_role="OPS",x_customer_scope=s["customer_code"],x_office_scope=s["office_code"],x_branch_scope=s["branch_code"])
    assert screen_data("agent-tasks::booking",job_ref="50001",**kwargs)["rows"]
    assert related("50001",**kwargs)["identity"]["job_ref"]=="50001"
    assert workflow("50001",**kwargs)["job_ref"]=="50001"
    assert all((not r.get("job_ref")) or r["job_ref"]=="50001" for r in search("50001",role="OPS",x_customer_scope=s["customer_code"],x_office_scope=s["office_code"],x_branch_scope=s["branch_code"])["record_hits"])
    with pytest.raises(HTTPException) as exc:
        related("50001",x_role="OPS",x_customer_scope="WRONG-CUSTOMER")
    assert exc.value.status_code==404
    with pytest.raises(HTTPException) as exc:
        workflow("50001",x_role="OPS",x_office_scope="WRONG-OFFICE")
    assert exc.value.status_code==404
    with pytest.raises(HTTPException) as exc:
        screen_data("agent-tasks::booking",job_ref="50001",x_role="OPS",x_branch_scope="WRONG-BRANCH")
    assert exc.value.status_code==404


def test_domain_audit_and_exception_feeds_are_agent_scoped(isolated):
    s=job_scope()
    good=audit_list(job_ref="50001",x_role="AGENT",x_agent_scope=s["agent_code"])
    assert all(r.get("job_ref")=="50001" for r in good)
    assert audit_list(job_ref="50001",x_role="AGENT",x_agent_scope="WRONG-AGENT")==[]
    good_exc=exception_list(job_ref="50001",x_role="AGENT",x_agent_scope=s["agent_code"])
    assert all(r.get("job_ref")=="50001" for r in good_exc)
    assert exception_list(job_ref="50001",x_role="AGENT",x_agent_scope="WRONG-AGENT")==[]


def test_quick_view_and_export_use_only_already_scoped_screen_rows(isolated):
    s=job_scope()
    out=screen_data("agent-tasks::booking",x_role="AGENT",x_agent_scope=s["agent_code"])
    assert all(r["agent"]==s["agent_code"] for r in out["rows"])
    # Quick View / Print / Export are client-side transforms of this scoped row set.
    for action in ("quick-view","print","export"):
        assert action_route("agent-tasks::booking",action,1,1,role="AGENT")["mode"]=="CLIENT_OR_CLX011"
