from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

def bl_block():
    s=HTML.index("async function clx49RenderBlTab(tab){")
    e=HTML.index("function clx49BookingGroup",s)
    return HTML[s:e]

def test_exact_same_page_tab_order():
    assert "const CLX49_BL_TABS=['Booking Info','Release Instruction','Delivery Order','Lock Info','Authorization']" in HTML

def test_bl_master_sections_and_fields():
    b=bl_block()
    for x in ['B/L Basic Info & Routing','Parties','Cargo & Equipment','Freight & Commercial Context']:
        assert x in b
    for x in ['B/L No.','B/L Type','MBL','HBL','Booking No.','Job Ref','Issue Date','Place of Issue','Original / Express',
              'Freight Term','Service Type','Carrier','Vessel','Voyage','POL','POT','POD','Final Destination',
              'Shipper','Actual Shipper','Consignee','Notify Party','Also Notify','Forwarding Agent','Delivery Agent',
              'Principal','Shipping Line','Agent Reference','Container / Seal','Packages','Package Type','Gross Weight',
              'Net Weight','Measurement','Marks & Numbers','Goods Description','Commodity','IMO / DG']:
        assert x in b,x

def test_release_instruction_uses_existing_delivery_order():
    assert "if(tab==='Release Instruction')return {module:'delivery-order',record:w.delivery_order" in HTML
    b=bl_block()
    for x in ['Release Type / Instruction','Original B/L Status','Telex Release','Payment Block','Customs Status','Freight Status','Valid Until','Release Status']:
        assert x in b

def test_delivery_order_fields():
    b=bl_block()
    for x in ['DO No.','Delivery Party','Validity','Release Status','Payment Block','Customs Status','Freight Status']:
        assert x in b

def test_lock_info_no_raw_json():
    b=bl_block()
    assert 'Lock Info / Holds / Authorized Release' in b
    assert 'Workflow Holds / Release' not in b
    assert "JSON.stringify({workflow:w.lock_info.workflow" not in b
    for x in ['Lock Status','Locked By','Lock Date','Lock Reason','Lock Scope','Unlock Approval']:
        assert x in HTML

def test_authorization_governance_and_audit():
    b=bl_block()
    for x in ['Draft Approval','Original / Express','Issue Date','Issue Authorization','Maker / Checker / Audit']:
        assert x in b
    assert 'approveClx049Bl' in b
    assert 'clx82AuthorizationAudit' in HTML

def test_switch_and_split_are_separate_linked_screens():
    b=bl_block()
    assert "agent-tasks::switch-bl" in b
    assert "agent-tasks::split-bl" in b
    assert "function clx49RenderSwitchBl" in HTML

def test_preview_is_read_only_and_same_data():
    assert 'function clx82BlPreview' in HTML
    assert 'Read-only B/L Preview' in HTML
    assert 'data-clx49-field' not in HTML[HTML.index("function clx82BlPreview"):HTML.index("function clx82ShowPreview") if "function clx82ShowPreview" in HTML else HTML.index("function clx49RecordForTab")]

def test_finance_and_container_use_existing_apis():
    assert "/api/clx070/container-control/booking-context/" in HTML
    assert "/api/gl/job/" in HTML
    assert "/api/clx046/job/" in HTML

def test_no_new_model():
    m=json.loads((ROOT/'CLX082_BL_FIELD_MATRIX.json').read_text())
    a=json.loads((ROOT/'CLX082_UI_ACCEPTANCE.json').read_text())
    assert m['backend_model_changed'] is False
    assert m['duplicate_bl_model_created'] is False
    assert a['backend_model_changed'] is False
    assert a['database_changed'] is False

def test_safety():
    a=json.loads((ROOT/'CLX082_UI_ACCEPTANCE.json').read_text())
    assert a['REAL_MONEY_READY'] is False
    assert a['REAL_PROVIDER_READY'] is False
