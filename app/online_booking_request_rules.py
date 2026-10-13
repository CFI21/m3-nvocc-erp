"""Online Agent Booking Request lifecycle. Separate from operational Booking/Job.

These transition rules are intentionally independent of persistence and do not
create jobs, issue booking references or grant permissions.
"""

from fastapi import HTTPException

ONLINE_REQUEST_TRANSITIONS = {
    "DRAFT": {"SUBMIT": "SUBMITTED"},
    "SUBMITTED": {"APPROVE": "APPROVED", "REJECT": "REJECTED"},
    "REJECTED": {},
    "APPROVED": {"CONVERT": "CONVERTED"},
    "CONVERTED": {},
}


def online_request_transition(status: str, action: str, *, approved: bool = False,
                              linked_job_ref: str | None = None) -> str:
    """Validate state only. Authorization and atomic conversion belong to the API."""
    current = str(status or "").upper()
    operation = str(action or "").upper()
    if current == "CONVERTED" and operation == "CONVERT" and linked_job_ref:
        return "CONVERTED"  # Idempotent response must reuse the linked job.
    target = ONLINE_REQUEST_TRANSITIONS.get(current, {}).get(operation)
    if target is None:
        raise HTTPException(409, {"code": "ONLINE_REQUEST_INVALID_TRANSITION"})
    if operation == "CONVERT" and (not approved or linked_job_ref):
        raise HTTPException(409, {"code": "ONLINE_REQUEST_CONVERSION_NOT_ALLOWED"})
    return target


ONLINE_REQUEST_TYPES = frozenset({"EXPORT", "IMPORT", "TRANSSHIPMENT"})


def validate_online_request_input(kind: str, fields: dict) -> dict:
    """Pre-job payload contract: no client-issued job, approval, or booking refs."""
    from copy import deepcopy
    if str(kind or "").upper() not in ONLINE_REQUEST_TYPES:
        raise HTTPException(422, {"code": "ONLINE_REQUEST_TYPE_INVALID"})
    if not isinstance(fields, dict):
        raise HTTPException(422, {"code": "ONLINE_REQUEST_FIELDS_INVALID"})
    forbidden = {"job_ref", "job no", "job number", "booking_ref", "approval_ref",
                 "approved by", "approved date", "converted_job_ref", "status"}
    if any(str(k).strip().lower() in forbidden for k in fields):
        raise HTTPException(422, {"code": "ONLINE_REQUEST_SERVER_FIELDS_FORBIDDEN"})
    return {"request_type": str(kind).upper(), "fields": deepcopy(fields)}
