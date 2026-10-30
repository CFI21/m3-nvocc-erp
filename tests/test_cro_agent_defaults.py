import pytest

from app.cro_agent_defaults import CROAgentLinkError, map_agent_cro_defaults


BASE = dict(
    booking_agent_code="UAT-AGENT-001",
    agent_record_key="UAT-AGENT-001",
    agent_status="ACTIVE",
    agent_version=3,
    approved_agent_payload={
        "bs_code": "UAT-BS-001",
        "operator_code": "UAT-OP-001",
        "cro_remarks": "UAT CRO approval test",
        "noc_note": "UAT NOC test only",
    },
)


def test_maps_four_authoritative_fields_with_source_version():
    result = map_agent_cro_defaults(**BASE)
    assert result["agent_master_version"] == 3
    assert result["agent_code"] == "UAT-AGENT-001"
    assert result["draft_fields"] == BASE["approved_agent_payload"]
    assert result["agent_cro_defaults_snapshot"] == BASE["approved_agent_payload"]


def test_draft_operator_changes_remain_intact():
    result = map_agent_cro_defaults(**BASE, draft_fields={"cro_remarks": "Custom instruction", "release_date": "2026-11-02"})
    assert result["draft_fields"]["cro_remarks"] == "Custom instruction"
    assert result["draft_fields"]["release_date"] == "2026-11-02"
    assert result["agent_cro_defaults_snapshot"]["cro_remarks"] == "UAT CRO approval test"


@pytest.mark.parametrize("overrides,code", [
    ({"booking_agent_code": "UAT-OTHER"}, "CRO_AGENT_BOOKING_MISMATCH"),
    ({"agent_status": "PENDING"}, "CRO_AGENT_NOT_ACTIVE"),
    ({"agent_status": "INACTIVE"}, "CRO_AGENT_NOT_ACTIVE"),
    ({"agent_version": 0}, "CRO_AGENT_VERSION_INVALID"),
    ({"agent_version": True}, "CRO_AGENT_VERSION_INVALID"),
])
def test_rejects_unauthorized_or_unapproved_links(overrides, code):
    with pytest.raises(CROAgentLinkError, match=code):
        map_agent_cro_defaults(**{**BASE, **overrides})


def test_does_not_mutate_input_payload_or_draft():
    draft = {"cro_remarks": "Edited"}
    result = map_agent_cro_defaults(**BASE, draft_fields=draft)
    result["agent_cro_defaults_snapshot"]["bs_code"] = "Changed"
    result["draft_fields"]["cro_remarks"] = "Changed"
    assert draft == {"cro_remarks": "Edited"}
    assert BASE["approved_agent_payload"]["bs_code"] == "UAT-BS-001"
