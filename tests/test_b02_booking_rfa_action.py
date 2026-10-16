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
    assert "BOOKING_APPROVAL_REFERENCE_ISSUANCE_NOT_READY" in source
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
