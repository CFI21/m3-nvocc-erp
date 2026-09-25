import datetime

from app.operations_workbench import _priority, _parse_dt, _age, PRIORITY_ORDER


def test_priority_model():
    assert _priority("PAYMENT_RELEASE_BLOCK")=="HIGH"
    assert _priority("FAILED_INTEGRATION")=="HIGH"
    assert _priority("DOCUMENT_GAP")=="MEDIUM"
    assert PRIORITY_ORDER["CRITICAL"] < PRIORITY_ORDER["HIGH"] < PRIORITY_ORDER["MEDIUM"]


def test_cutoff_datetime_parser():
    d=_parse_dt("2026-09-26T10:00:00+00:00")
    assert d is not None
    assert d.tzinfo is not None
    assert _parse_dt("not-a-date") is None


def test_age_is_never_negative():
    future=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=5)).isoformat()
    assert _age(future)==0


def test_workbench_source_preserves_underlying_contract():
    from app.operations_workbench import BulkAction, READ_ROLES, WRITE_ROLES
    body=BulkAction(exception_keys=["JOB:1:DOC"],owner="ops-1")
    assert body.exception_keys==["JOB:1:DOC"]
    assert "AGENT" in READ_ROLES
    assert "AGENT" not in WRITE_ROLES
    assert {"ADMIN","OPS","DOCS","FINANCE"} <= WRITE_ROLES
