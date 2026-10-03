import datetime

import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.state_transition_governance import (
    authorize_container_transition,
    transition_history,
)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"item4.db")
    monkeypatch.setenv("M3_STATE_TRANSITION_GOVERNANCE_ENABLED","true")
    seed_run(True)
    return db.DB_PATH


def recent(minutes=5):
    return (datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=minutes)).isoformat()


def test_standard_transition_uses_existing_clx071_flow(isolated):
    out=authorize_container_transition(
        container_no="TSTU0000001",from_state="AVAILABLE",to_state="RESERVED",
        mode="STANDARD",actor_user_id="USR-OPS-001",actor_role="OPS",
    )
    assert out["allowed"] is True
    assert out["mode"]=="STANDARD"


def test_invalid_standard_transition_is_blocked_and_logged(isolated):
    with pytest.raises(HTTPException) as e:
        authorize_container_transition(
            container_no="TSTU0000001",from_state="AVAILABLE",to_state="DISCHARGED",
            mode="STANDARD",actor_user_id="USR-OPS-001",actor_role="OPS",
        )
    assert e.value.detail["code"]=="INVALID_STATE_TRANSITION"
    rows=transition_history("TSTU0000001")
    assert rows[-1]["outcome"]=="BLOCKED"


def test_recent_correction_requires_reason_source_and_is_allowed(isolated):
    out=authorize_container_transition(
        container_no="TSTU0000002",from_state="GATE_IN",to_state="STUFFED",
        mode="CORRECTION",actor_user_id="USR-OPS-002",actor_role="OPS",
        reason="Synthetic factual correction",source_event_ref="EVT-TEST-001",
        original_transition_at=recent(5),
    )
    assert out["allowed"] is True


def test_expired_correction_is_blocked(isolated):
    with pytest.raises(HTTPException) as e:
        authorize_container_transition(
            container_no="TSTU0000002",from_state="GATE_IN",to_state="STUFFED",
            mode="CORRECTION",actor_user_id="USR-OPS-002",actor_role="OPS",
            reason="Synthetic factual correction",source_event_ref="EVT-TEST-001",
            original_transition_at=recent(30),
        )
    assert e.value.detail["code"]=="CORRECTION_WINDOW_EXPIRED"


def test_override_requires_manager_and_reason(isolated):
    with pytest.raises(HTTPException) as e:
        authorize_container_transition(
            container_no="TSTU0000003",from_state="AVAILABLE",to_state="LOADED",
            mode="OVERRIDE",actor_user_id="USR-OPS-003",actor_role="OPS",
            reason="Synthetic test",
        )
    assert e.value.detail["code"]=="OVERRIDE_REQUIRES_MANAGER_AND_REASON"

    out=authorize_container_transition(
        container_no="TSTU0000003",from_state="AVAILABLE",to_state="LOADED",
        mode="OVERRIDE",actor_user_id="USR-MGR-001",actor_role="EQUIPMENT_MANAGER",
        reason="Synthetic authorized override",
    )
    assert out["allowed"] is True


def test_exception_resolution_requires_dual_human_approval_and_evidence(isolated):
    with pytest.raises(HTTPException) as e:
        authorize_container_transition(
            container_no="TSTU0000004",from_state="INSPECTION",to_state="TOTAL_LOSS",
            mode="EXCEPTION_RESOLUTION",actor_user_id="USR-MAKER-001",actor_role="EQUIPMENT_MANAGER",
            reason="Synthetic total loss resolution",evidence_refs=["EVID-001"],
            approver_user_ids=["USR-APPROVER-001"],
        )
    assert e.value.detail["code"]=="DUAL_HUMAN_APPROVAL_REQUIRED"

    out=authorize_container_transition(
        container_no="TSTU0000004",from_state="INSPECTION",to_state="TOTAL_LOSS",
        mode="EXCEPTION_RESOLUTION",actor_user_id="USR-MAKER-001",actor_role="EQUIPMENT_MANAGER",
        reason="Synthetic total loss resolution",evidence_refs=["EVID-001","EVID-002"],
        approver_user_ids=["USR-APPROVER-001","USR-APPROVER-002"],
    )
    assert out["allowed"] is True


def test_exception_resolution_rejects_maker_as_approver(isolated):
    with pytest.raises(HTTPException) as e:
        authorize_container_transition(
            container_no="TSTU0000005",from_state="INSPECTION",to_state="REPAIR",
            mode="EXCEPTION_RESOLUTION",actor_user_id="USR-MAKER-001",actor_role="EQUIPMENT_MANAGER",
            reason="Synthetic exception",evidence_refs=["EVID-001"],
            approver_user_ids=["USR-MAKER-001","USR-APPROVER-002"],
        )
    assert e.value.detail["code"]=="MAKER_CHECKER_SAME_USER"


def test_financial_posted_transition_requires_reversal(isolated):
    with pytest.raises(HTTPException) as e:
        authorize_container_transition(
            container_no="TSTU0000006",from_state="DISCHARGED",to_state="GATE_OUT_FULL",
            mode="STANDARD",actor_user_id="USR-OPS-006",actor_role="OPS",
            financial_posted=True,
        )
    assert e.value.detail["code"]=="FINANCIAL_POSTED_REVERSAL_REQUIRED"
    assert transition_history("TSTU0000006")[-1]["outcome"]=="BLOCKED"


def test_transition_history_is_immutable(isolated):
    authorize_container_transition(
        container_no="TSTU0000007",from_state="AVAILABLE",to_state="RESERVED",
        mode="STANDARD",actor_user_id="USR-OPS-007",actor_role="OPS",
    )
    c=db.connect()
    with pytest.raises(Exception):
        c.execute("UPDATE state_transition_events SET to_state='LOADED' WHERE subject_ref='TSTU0000007'")
    with pytest.raises(Exception):
        c.execute("DELETE FROM state_transition_events WHERE subject_ref='TSTU0000007'")
    c.close()


def test_feature_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("M3_STATE_TRANSITION_GOVERNANCE_ENABLED",raising=False)
    with pytest.raises(HTTPException) as e:
        authorize_container_transition(
            container_no="TSTU0000008",from_state="AVAILABLE",to_state="RESERVED",
            mode="STANDARD",actor_user_id="USR-OPS-008",actor_role="OPS",
        )
    assert e.value.detail["code"]=="STATE_TRANSITION_GOVERNANCE_DISABLED"
