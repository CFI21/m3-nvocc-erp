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
    assert "payload['RFA Submitted By']=maker_user" in source
    assert "BOOKING_RFA_REQUIRES_IAM_SESSION" in source
    assert "BOOKING_RFA_MAKER_IDENTITY_REQUIRED" in source
    assert "BOOKING_FOUR_EYES_VIOLATION" in source
    assert "submitting_user==checker_user" in source
    assert source.index("BOOKING_FOUR_EYES_VIOLATION")<source.index("payload['Approval #']=number")


def test_booking_rfa_checker_approval_ui_wired_to_existing_api():
    from pathlib import Path
    html=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    assert 'async function clx81ApproveBookingRfa(id,version)' in html
    assert '/api/v1/booking/' in html
    assert "['ADMIN','SUPER_ADMIN'].includes(state.role)" in html
    assert 'Approve RFA</button>' in html


def test_governed_job_actor_fails_closed_without_allow_role():
    from app.main import governed_job_actor
    code=inspect.getsource(governed_job_actor)
    assert "GOVERNED_JOB_ROLE_NOT_GRANTED" in code
    assert "role=roles[0] if roles else 'SUPER_ADMIN'" not in code



def test_governed_job_actor_denies_empty_allow_matches_runtime(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from app import main
    monkeypatch.setattr(main, 'bulk_permission_enabled', lambda: True)
    monkeypatch.setattr(main, 'iam_session', lambda conn, token: {'user_id': 42})
    monkeypatch.setattr(main, 'bulk_assert_job_access',
                        lambda conn, user, job, module, action: {'matches': []})
    with pytest.raises(HTTPException) as denied:
        main.governed_job_actor(None, 'isolated-test-session', '50001', 'approve')
    assert denied.value.status_code == 403
    assert denied.value.detail['code'] == 'GOVERNED_JOB_ROLE_NOT_GRANTED'


def test_governed_job_actor_ignores_explicit_deny_matches(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from app import main
    monkeypatch.setattr(main, 'bulk_permission_enabled', lambda: True)
    monkeypatch.setattr(main, 'iam_session', lambda conn, token: {'user_id': 42})
    monkeypatch.setattr(main, 'bulk_assert_job_access',
                        lambda conn, user, job, module, action: {
                            'matches': [{'effect': 'DENY', 'role': 'SUPER_ADMIN'}]
                        })
    with pytest.raises(HTTPException) as denied:
        main.governed_job_actor(None, 'isolated-test-session', '50001', 'approve')
    assert denied.value.status_code == 403


def test_booking_approval_records_trusted_checker_audit_fields():
    from app.main import update_record
    approval=inspect.getsource(action_record)
    update=inspect.getsource(update_record)
    assert "payload['Approved By']=checker_user" in approval
    assert "payload['Approved At']=now()" in approval
    assert "'Approved By','Approved At','RFA Submitted By'" in update


def test_booking_approval_fails_closed_without_masterdata_sequence_table():
    source=inspect.getsource(action_record)
    assert "sqlite_master WHERE type='table' AND name='md_sequences'" in source
    assert source.index("if not sequence_table:")<source.index("SELECT id,prefix,next_number,width FROM md_sequences")
    assert "BOOKING_APPROVAL_SEQUENCE_NOT_CONFIGURED" in source


def test_booking_approval_revalidates_before_sequence_allocation():
    approval=inspect.getsource(action_record).split("if module=='booking' and action=='approve':",1)[1].split("if module=='booking' and action=='submit-rfa':",1)[0]
    assert 'approval_errors=validate_fields(module,ctx,payload)' in approval
    assert 'assert_booking_rules(conn,ctx,payload,tid)' in approval
    assert approval.index('assert_booking_rules(conn,ctx,payload,tid)') < approval.index('UPDATE md_sequences SET next_number')


def test_booking_hold_cancel_cannot_mutate_approved_or_terminal_records():
    source=inspect.getsource(action_record)
    assert "action in {'hold','cancel'}" in source
    assert "BOOKING_TERMINAL_STATUS_LOCK" in source
    assert source.index("BOOKING_TERMINAL_STATUS_LOCK")<source.index("elif action=='cancel':")


def test_booking_rfa_requires_iam_before_transaction_access():
    code=inspect.getsource(action_record)
    assert "BOOKING_APPROVAL_FLOW_REQUIRES_IAM" in code
    assert code.index("BOOKING_APPROVAL_FLOW_REQUIRES_IAM")<code.index("conn=connect(); tx(conn)")

def test_booking_approval_requires_iam_before_transaction_access():
    code=inspect.getsource(action_record)
    assert "BOOKING_APPROVAL_FLOW_REQUIRES_IAM" in code
    assert code.index("BOOKING_APPROVAL_FLOW_REQUIRES_IAM") < code.index("conn=connect(); tx(conn)")
    assert "action in {'submit-rfa','approve'}" in code


def test_closed_booking_job_uses_existing_audited_guard():
    code=inspect.getsource(action_record)
    assert "CLOSED_JOB_BLOCKED" in code
    assert "JOB_CLOSED" in code
    assert code.index("CLOSED_JOB_BLOCKED")<code.index("sequence_code='BOOKING_APPROVAL'")



def test_booking_draft_create_rejects_client_supplied_approval_and_maker_fields():
    from app.main import create_record
    source=inspect.getsource(create_record)
    assert "('Approval #','Approval Ref','Approval Reference','Approved By','Approved At','RFA Submitted By')" in source
    assert "if any(k in body.fields" in source
    assert "BOOKING_APPROVAL_REFERENCE_ACTION_REQUIRED" in source


def test_booking_approval_rejects_invalid_sequence_config_before_increment():
    approval=inspect.getsource(action_record)
    assert "BOOKING_APPROVAL_SEQUENCE_INVALID" in approval
    assert "len(str(next_number))>width" in approval
    assert approval.index("BOOKING_APPROVAL_SEQUENCE_INVALID")<approval.index("UPDATE md_sequences SET next_number")


def test_booking_approval_rejects_duplicate_historical_reference_before_consuming_sequence():
    source=inspect.getsource(action_record)
    assert "BOOKING_APPROVAL_REFERENCE_DUPLICATE" in source
    assert "json_extract(payload_json" in source
    assert source.index("BOOKING_APPROVAL_REFERENCE_DUPLICATE") < source.index("UPDATE md_sequences SET next_number")


def test_duplicate_approval_check_handles_invalid_legacy_json_safely():
    import sqlite3
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE transaction_records(id INTEGER,module TEXT,payload_json TEXT)')
    db.executemany('INSERT INTO transaction_records VALUES(?,?,?)',[
        (1,'booking','{invalid'),
        (2,'booking','{"Approval #":"BA00001"}'),
        (3,'booking','{"Approval #":"BA00002"}'),
    ])
    sql="""SELECT id FROM transaction_records WHERE module='booking' AND id<>? AND CASE WHEN json_valid(payload_json) THEN json_extract(payload_json, '$."Approval #"') ELSE NULL END = ? LIMIT 1"""
    row=db.execute(sql,(3,'BA00001')).fetchone()
    assert row==(2,)
    assert db.execute(sql,(3,'BA99999')).fetchone() is None


def test_booking_rfa_missing_session_identity_fails_closed():
    source=inspect.getsource(action_record)
    assert "BOOKING_RFA_MAKER_IDENTITY_MISSING" in source
    assert "ga['session'].get('user_ref')" in source
    assert source.index("BOOKING_RFA_MAKER_IDENTITY_MISSING")<source.index("payload['RFA Submitted By']=maker_user")


def test_approved_booking_readonly_audit_visible_in_reference_workspace():
    from pathlib import Path
    ui=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    grid=ui.split('function clx81BookingReferenceGrid(w,r){',1)[1].split('\nfunction ',1)[0]
    assert 'const approvalAudit=approved?' in grid
    for key in ('Approval #','Approved By','Approved At'):
        assert f"val('{key}')" in grid
    assert 'approvalAudit+' in grid
    assert 'input readonly' in grid


def test_booking_reference_grid_locks_editable_inputs_after_draft():
    from pathlib import Path
    ui=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    grid=ui.split('function clx81BookingReferenceGrid(w,r){',1)[1].split('\nfunction ',1)[0]
    assert "const bookingLocked=saved&&String(r?.status||p.Status||'').trim().toLowerCase()!=='draft'" in grid
    assert "locked=viewerReadOnly()||bookingLocked" in grid


def test_grid_change_refuses_mutation_outside_booking_draft():
    from pathlib import Path
    ui=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    handler=ui.split('function clx81GridChange(el){',1)[1].split('\nfunction ',1)[0]
    assert "String(r.status||r.fields?.Status||'').trim().toLowerCase()!=='draft'" in handler
    assert handler.index("!=='draft'") < handler.index("clx81BookingDraft.values[key]=el.value")


def test_booking_cargo_popup_is_readonly_after_draft():
    from pathlib import Path
    ui=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    cargo=ui.split('function clx81OpenCargoDetails(){',1)[1].split('// Requested nine external',1)[0]
    assert "const cargoLocked=viewerReadOnly()||String(r.status||r.fields?.Status||'').trim().toLowerCase()!=='draft'" in cargo
    assert "(cargoLocked?' disabled':'')" in cargo
    assert "(viewerReadOnly()?' disabled':'')" not in cargo


def test_cargo_capture_handler_rejects_non_draft_booking():
    from pathlib import Path
    ui=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    fn=ui.split('function clx81CaptureCargoDetails(){',1)[1].split('\nfunction ',1)[0]
    assert "String(r.status||r.fields?.Status||'').trim().toLowerCase()!=='draft'" in fn
    assert fn.index("!=='draft'")<fn.index("clx81BookingDraft.values[el.dataset.clx81Cargo]")


def test_cargo_popup_hides_apply_button_after_draft():
    from pathlib import Path
    ui=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    popup=ui.split('function clx81OpenCargoDetails(){',1)[1].split('// Requested nine external',1)[0]
    assert "cargoLocked?'':" in popup
    assert "Apply to Booking Draft</button>" in popup
