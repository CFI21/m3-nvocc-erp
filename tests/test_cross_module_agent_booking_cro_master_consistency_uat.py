"""Executable cross-module Agent -> Booking -> CRO consistency contracts.

Pure in-memory fixtures: no Neon, Render or production transactions.
"""
import pytest

from app.cro_agent_defaults import CROAgentLinkError, map_agent_cro_defaults
from app.screen_catalog import build_catalog, AGENT_SETUP_ALIASES, HO_TRANSACTION_ALIASES

APPROVED={"bs_code":"BS-RTM","operator_code":"OPS-RTM","cro_remarks":"Approved master","noc_note":"Approved NOC"}

def _map(**overrides):
    params=dict(booking_agent_code="AG-RTM",agent_record_key="AG-RTM",
                agent_status="ACTIVE",agent_version=3,
                approved_agent_payload=APPROVED,draft_fields={})
    params.update(overrides)
    return map_agent_cro_defaults(**params)

def test_ho_and_agent_opening_have_one_master_and_cro_is_existing_transaction():
    catalog=build_catalog()
    ids={s["screen_id"] for s in catalog["screens"]}
    ho={v["label"]:v["target"] for v in HO_TRANSACTION_ALIASES}
    agent={v["label"]:v["target"] for v in AGENT_SETUP_ALIASES}
    assert ho["Agent Opening"]==agent["Agent"]=="master-data::agents"
    assert ho["Customer Opening"]=="master-data::customers"
    assert {"agent-tasks::booking","agent-tasks::cro","agent-tasks::container-activity",
            "gl-accounts::invoice","gl-accounts::payment"}<=ids

def test_only_approved_matching_agent_defaults_reach_cro_draft():
    result=_map()
    assert result["agent_code"]=="AG-RTM"
    assert result["agent_master_version"]==3
    assert result["draft_fields"]["bs_code"]=="BS-RTM"
    assert result["agent_cro_defaults_snapshot"]==APPROVED

def test_cro_draft_override_does_not_mutate_approved_agent_master():
    approved=dict(APPROVED)
    result=_map(draft_fields={"bs_code":"LOCAL-OVERRIDE"})
    assert result["draft_fields"]["bs_code"]=="LOCAL-OVERRIDE"
    assert result["agent_cro_defaults_snapshot"]["bs_code"]=="BS-RTM"
    result["agent_cro_defaults_snapshot"]["bs_code"]="CHANGED"
    assert APPROVED==approved

@pytest.mark.parametrize("changes,error",[
    ({"booking_agent_code":"OTHER"},"CRO_AGENT_BOOKING_MISMATCH"),
    ({"agent_status":"PENDING"},"CRO_AGENT_NOT_ACTIVE"),
    ({"agent_version":0},"CRO_AGENT_VERSION_INVALID"),
])
def test_incompatible_agent_or_booking_never_supplies_cro_defaults(changes,error):
    with pytest.raises(CROAgentLinkError,match=error):
        _map(**changes)
