from app.main import validate_fields


def _job():
    return {
        'job_ref': '50001',
        'agent_code': 'CLX-AGT-SIN',
        'customer_name': 'CLX Customer 1',
        'booking_ref': 'BKG-50001',
        'pol': 'NLRTM',
        'pod': 'SGSIN',
        'hbl_no': 'HBL-50001',
        'container_no': 'MSCU5000101',
    }


def test_container_activity_accepts_container_bound_to_job():
    errs = validate_fields(
        'container-activity',
        _job(),
        {'Job Ref': '50001', 'Container': 'MSCU5000101'},
    )
    assert 'CONTAINER_JOB_MISMATCH' not in errs


def test_container_activity_rejects_container_from_another_job():
    errs = validate_fields(
        'container-activity',
        _job(),
        {'Job Ref': '50001', 'Container': 'MSCU9999999'},
    )
    assert 'CONTAINER_JOB_MISMATCH' in errs


def test_container_activity_container_is_optional_when_event_has_no_explicit_container_field():
    errs = validate_fields(
        'container-activity',
        _job(),
        {'Job Ref': '50001'},
    )
    assert 'CONTAINER_JOB_MISMATCH' not in errs
