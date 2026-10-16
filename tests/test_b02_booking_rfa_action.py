"""B02 P2: Booking RFA uses existing governed transaction action."""
import inspect
from app.main import action_record

def test_rfa_action_must_start_from_draft_and_recheck_permissions():
    source=inspect.getsource(action_record)
    assert "action=='submit-rfa'" in source
    assert "BOOKING_DRAFT_REQUIRED_FOR_RFA" in source
    assert "booking_iam_agent_granted" in source
    assert "booking_validate_authorized_ports" in source
    assert "assert_booking_rules(conn,ctx,payload,tid)" in source
    assert "new_status='RFA'" in source

def test_approval_issuance_remains_blocked_until_governed_reference_ready():
    source=inspect.getsource(action_record)
    assert "BOOKING_RFA_REQUIRED_BEFORE_APPROVAL" in source
    assert "BOOKING_APPROVAL_SEQUENCE_NOT_CONFIGURED" in source
    assert "BOOKING_INDEPENDENT_APPROVER_REQUIRED" in source
    assert "OPTIMISTIC_LOCK_CONFLICT" in source


def test_booking_ui_exposes_governed_rfa_action():
    from pathlib import Path
    html=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    assert 'async function clx81SubmitBookingRfa(id,version)' in html
    assert '/actions/submit-rfa' in html
    assert 'Save Draft</button>' in html
    assert 'Submit RFA</button>' in html


def test_rfa_prevents_unsaved_browser_values():
    from pathlib import Path
    ui=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    fn=ui.split('async function clx81SubmitBookingRfa(id,version){',1)[1].split('async function clx49RenderBooking(tab){',1)[0]
    assert "Unsaved Booking fields detected. Save Draft before Submit RFA." in fn
    assert "clx81BookingDraft?.values" in fn
    assert fn.index("const dirty=")<fn.index("/actions/submit-rfa")


def test_rfa_action_rejects_non_booking_modules_before_database_access():
    import pytest
    from fastapi import HTTPException
    from app.main import ActionBody
    with pytest.raises(HTTPException) as error:
        action_record('bl',1,'submit-rfa',ActionBody(version=1))
    assert error.value.status_code==422
    assert error.value.detail['code']=='BOOKING_RFA_ACTION_ONLY'


def test_booking_unknown_action_rejected_before_database_access():
    import pytest
    from fastapi import HTTPException
    from app.main import ActionBody
    with pytest.raises(HTTPException) as error:
        action_record('booking',1,'arbitrary-action',ActionBody(version=1))
    assert error.value.status_code==422
    assert error.value.detail['code']=='BOOKING_ACTION_NOT_SUPPORTED'


def test_closed_job_blocks_booking_rfa():
    import inspect
    code=inspect.getsource(action_record)
    assert "if ctx['closed'] and action in {'submit-rfa','approve'" in code
    assert code.index("if ctx['closed'] and action") < code.index("if module=='booking' and action=='submit-rfa':")


def test_booking_edits_locked_after_rfa():
    from app.main import update_record
    source=inspect.getsource(update_record)
    assert "BOOKING_DRAFT_ONLY_EDIT" in source
    assert source.index("BOOKING_DRAFT_ONLY_EDIT")<source.index("payload.update(body.fields)")
    from pathlib import Path
    html=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    booking=html.split('async function clx49RenderBooking(tab){',1)[1].split("if(tab==='Booking Info')",1)[0]
    assert "String(r.status||p.Status||'').toLowerCase()==='draft'" in booking


def test_booking_delete_locked_after_rfa():
    from app.main import delete_record
    source=inspect.getsource(delete_record)
    assert "BOOKING_DELETE_REQUIRES_DRAFT" in source
    assert source.index("BOOKING_DELETE_REQUIRES_DRAFT")<source.index("DELETE FROM transaction_records")


def test_booking_amend_requires_rfa_and_valid_agent_grants():
    code=inspect.getsource(action_record)
    amend=code.split("if module=='booking' and action=='amend':",1)[1].split("if module=='booking' and action=='approve'",1)[0]
    assert "BOOKING_RFA_REQUIRED_FOR_AMEND" in amend
    assert "booking_iam_agent_granted" in amend
    assert "booking_validate_authorized_ports" in amend
    assert "booking_agent_port_grants" in amend


def test_approval_uses_existing_governed_sequence_with_fail_closed_configuration():
    source=inspect.getsource(action_record)
    assert "BOOKING_APPROVAL_SEQUENCE_NOT_CONFIGURED" in source
    assert "sequence_code='BOOKING_APPROVAL' AND status='ACTIVE'" in source
    assert "BOOKING_INDEPENDENT_APPROVER_REQUIRED" in source
    assert "BOOKING_APPROVAL_SEQUENCE_CONFLICT" in source
    assert "payload['Approval #']=number" in source
    assert "BOOKING_APPROVAL_REFERENCE_ISSUANCE_NOT_READY" not in source


def test_booking_rfa_records_trusted_maker_and_approval_enforces_four_eyes():
    source=inspect.getsource(action_record)
    assert "payload['RFA Submitted By']=ga['session']['user_ref']" in source
    assert "BOOKING_RFA_REQUIRES_IAM_SESSION" in source
    assert "BOOKING_RFA_MAKER_IDENTITY_REQUIRED" in source
    assert "BOOKING_FOUR_EYES_VIOLATION" in source
    assert "submitting_user==checker_user" in source
    assert source.index("BOOKING_FOUR_EYES_VIOLATION")<source.index("payload['Approval #']=number")
