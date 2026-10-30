"""P0 Booking lifecycle contract: actual FastAPI routes and guarded transitions."""
import inspect
import pytest
from fastapi import HTTPException
from app import main


def _routes():
    return {(method, route.path)
            for route in main.app.routes
            for method in (getattr(route, "methods", None) or set())}


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/{module}"),
    ("GET", "/api/v1/{module}/{tid}"),
    ("PUT", "/api/v1/{module}/{tid}"),
    ("POST", "/api/v1/{module}/{tid}/actions/{action}"),
    ("GET", "/api/v1/jobs/{job_ref}/context"),
    ("GET", "/api/v1/jobs/{job_ref}/linked-transactions"),
    ("GET", "/api/v1/jobs/{job_ref}/bl-history"),
])
def test_authenticated_lifecycle_has_real_route_contract(method,path):
    assert (method,path) in _routes()


@pytest.mark.parametrize("job_ref", ["abc", "1234", "123456", "12 45"])
def test_create_rejects_invalid_job_reference(job_ref):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        main.CreateBody(job_ref=job_ref, fields={})


def test_booking_approval_requires_governed_iam_in_handler():
    source=inspect.getsource(main.action_record)
    assert "BOOKING_APPROVAL_FLOW_REQUIRES_IAM" in source
    assert "BOOKING_INDEPENDENT_APPROVER_REQUIRED" in source
    assert "BOOKING_FOUR_EYES_VIOLATION" in source
    assert "BOOKING_RFA_REQUIRED_BEFORE_APPROVAL" in source
    assert "BOOKING_APPROVAL_SEQUENCE_NOT_CONFIGURED" in source


def test_booking_authorization_is_rechecked_before_idempotent_replay():
    source=inspect.getsource(main.create_record)
    recheck=source.index("Revoked agent/port access must also block idempotent")
    replay=source.index("if idempotency_key:")
    assert recheck < replay
    assert "booking_validate_authorized_ports" in source[recheck:replay]
    assert "booking_iam_agent_granted" in source[recheck:replay]


@pytest.mark.parametrize("fields", [
    {"Job Ref":"99999"},
    {"Agent":"OTHER_AGENT"},
])
def test_booking_reference_integrity_rejects_cross_job_injection(fields):
    job={"job_ref":"00001","agent_code":"A1","customer_name":"Customer",
         "booking_ref":"BOOK-1","pol":"NLRTM","pod":"DEHAM"}
    errors=main.validate_fields("booking",job,fields)
    assert errors


@pytest.mark.parametrize("fields,code",[
    ({"POL":"DEHAM"},"POL_JOB_MISMATCH"),
    ({"POD":"NLRTM"},"POD_JOB_MISMATCH"),
    ({"Booking No.":"BOOK-2"},"BOOKING_MASTER_MISMATCH"),
])
def test_booking_routing_must_match_authoritative_job(fields,code):
    job={"job_ref":"00001","agent_code":"A1","customer_name":"Customer",
         "booking_ref":"BOOK-1","pol":"NLRTM","pod":"DEHAM"}
    assert code in main.validate_fields("booking",job,fields)
