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
