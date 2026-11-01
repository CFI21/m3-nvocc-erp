"""Pure mapping of approved Agent Master CRO defaults into operational CRO drafts.

No database writes. Caller must load an ACTIVE, approved md_records agent and
an authorized Booking/job; this helper never treats a pending change as approved.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

CRO_AGENT_DEFAULT_KEYS = ("bs_code", "operator_code", "cro_remarks", "noc_note")


class CROAgentLinkError(ValueError):
    """The Agent/Booking linkage is not safe for creating a CRO draft."""


def map_agent_cro_defaults(
    *,
    booking_agent_code: str,
    agent_record_key: str,
    agent_status: str,
    agent_version: int,
    approved_agent_payload: Mapping[str, Any],
    draft_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return linked CRO draft context and source snapshot, without persistence.

    Existing explicit draft values take priority over Agent default suggestions.
    The snapshot records exactly the source Agent values at mapping time;
    on CRO approval the transaction must persist a fresh, approved snapshot.
    """
    booking_agent = str(booking_agent_code or "").strip().upper()
    master_agent = str(agent_record_key or "").strip().upper()
    if not booking_agent or booking_agent != master_agent:
        raise CROAgentLinkError("CRO_AGENT_BOOKING_MISMATCH")
    if str(agent_status or "").upper() != "ACTIVE":
        raise CROAgentLinkError("CRO_AGENT_NOT_ACTIVE")
    if not isinstance(agent_version, int) or isinstance(agent_version, bool) or agent_version < 1:
        raise CROAgentLinkError("CRO_AGENT_VERSION_INVALID")
    if not isinstance(approved_agent_payload, Mapping):
        raise CROAgentLinkError("CRO_AGENT_PAYLOAD_INVALID")

    defaults = {key: deepcopy(approved_agent_payload.get(key) or "") for key in CRO_AGENT_DEFAULT_KEYS}
    fields = deepcopy(dict(draft_fields or {}))
    # Never overwrite CRO fields already edited by an operator in Draft.
    for key, value in defaults.items():
        fields.setdefault(key, value)
    return {
        "agent_code": master_agent,
        "agent_master_version": agent_version,
        "agent_cro_defaults_snapshot": defaults,
        "draft_fields": fields,
    }
