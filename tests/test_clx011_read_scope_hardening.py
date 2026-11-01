import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.screen_catalog import build_catalog
from app.screen_integration import screen_data, related, workflow, search
from tests.iam_personas import persona_session
from app.admin import Login, login


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "clx011_scope.db")
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH


def job_scope(job_ref):
    c=db.connect()
    try:
        r=c.execute("""SELECT j.job_ref,a.code agent_code,c.code customer_code,j.office_code,j.branch_code
                       FROM jobs j JOIN agents a ON a.id=j.agent_id JOIN customers c ON c.id=j.customer_id
                       WHERE j.job_ref=?""",(job_ref,)).fetchone()
        return dict(r)
    finally:
        c.close()


def test_frozen_screen_baseline_unchanged():
    c=build_catalog()
    assert c["screen_count"]==196
    assert c["extension_count"]==0


def test_agent_screen_data_requires_scope(isolated):
    with pytest.raises(HTTPException) as exc:
        screen_data("agent-tasks::booking",x_role="AGENT")
    assert exc.value.status_code==401
    assert exc.value.detail["code"]=="EQUIPMENT_SESSION_REQUIRED"
    token=persona_session(role="AGENT",agent_code="CLX-AGT-SIN",office_code="RTM")
    scoped=screen_data("agent-tasks::booking",x_role="SUPER_ADMIN",
                       x_agent_scope="WRONG-AGENT",x_m3_session=token)
    assert scoped["rows"], "Trusted RTM agent must see own seeded bookings"
    assert all(row["agent"]=="CLX-AGT-SIN" for row in scoped["rows"])


def test_agent_screen_data_filters_cross_agent_rows(isolated):
    s=job_scope("50001")
    token=persona_session(role="AGENT",agent_code=s["agent_code"],office_code="RTM")
    ok=screen_data("agent-tasks::booking",x_m3_session=token)
    assert ok["rows"]
    assert all(r["agent"]==s["agent_code"] for r in ok["rows"])
    forged=screen_data("agent-tasks::booking",x_role="SUPER_ADMIN",x_agent_scope="WRONG-AGENT",x_m3_session=token)
    assert forged["rows"]==ok["rows"]


def test_job_specific_direct_screen_access_is_opaque_outside_scope(isolated):
    s=job_scope("50001")
    token=persona_session(role="AGENT",agent_code=s["agent_code"],office_code="RTM")
    ok=screen_data("agent-tasks::booking",job_ref="50001",x_m3_session=token)
    assert ok["rows"] and all(r["job_ref"]=="50001" for r in ok["rows"])
    other=persona_session(role="AGENT",agent_code="WRONG-AGENT",office_code="RTM")
    with pytest.raises(HTTPException) as exc:
        screen_data("agent-tasks::booking",job_ref="50001",x_m3_session=other)
    assert exc.value.status_code==404


def test_customer_scope_applies_to_direct_screen_data(isolated):
    s=job_scope("50001")
    token=login(Login(username="admin",password="Admin123!",mfa_code="123456"))["session_token"]
    ok=screen_data("agent-tasks::booking",job_ref="50001",x_customer_scope=s["customer_code"],x_m3_session=token)
    assert ok["rows"]
    with pytest.raises(HTTPException) as exc:
        screen_data("agent-tasks::booking",job_ref="50001",x_customer_scope="WRONG-CUSTOMER",x_m3_session=token)
    assert exc.value.status_code==404


def test_related_and_workflow_cannot_bypass_agent_scope(isolated):
    s=job_scope("50001")
    token=persona_session(role="AGENT",agent_code=s["agent_code"],office_code="RTM")
    assert related("50001",x_m3_session=token)["identity"]["job_ref"]=="50001"
    assert workflow("50001",x_m3_session=token)["job_ref"]=="50001"
    other=persona_session(role="AGENT",agent_code="WRONG-AGENT",office_code="RTM")
    for fn in (related,workflow):
        with pytest.raises(HTTPException) as exc:
            fn("50001",x_m3_session=other)
        assert exc.value.status_code==404


def test_search_record_results_are_agent_scoped(isolated):
    s=job_scope("50001")
    token=persona_session(role="AGENT",agent_code=s["agent_code"],office_code="RTM")
    out=search("500",x_m3_session=token)
    job_refs={r.get("job_ref") for r in out["record_hits"] if r.get("job_ref")}
    c=db.connect()
    try:
        allowed={r["job_ref"] for r in c.execute("""SELECT j.job_ref FROM jobs j JOIN agents a ON a.id=j.agent_id WHERE a.code=?""",(s["agent_code"],)).fetchall()}
    finally:c.close()
    assert job_refs <= allowed


def test_role_visibility_still_blocks_direct_gl_screen_for_agent(isolated):
    token=persona_session(role="AGENT",agent_code=job_scope("50001")["agent_code"],office_code="RTM")
    with pytest.raises(HTTPException) as exc:
        screen_data("gl-accounts::invoice",x_role="SUPER_ADMIN",x_m3_session=token)
    assert exc.value.status_code==403

