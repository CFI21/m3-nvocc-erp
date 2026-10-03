from pathlib import Path
import json,re

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

def test_operational_key_scope():
    assert "const CLX83_OPERATIONAL_KEYS=new Set(['cro','trt','export-trt','transshipment-trt','delivery-order'])" in HTML
    assert "renderClx083OperationalMaster" in HTML

def test_cro_master_fields_and_authoritative_container_link():
    for x in ['CRO Basic Info / Routing','CRO No.','Booking No.','Job Ref','Customer','Carrier','Principal','Vessel','Voyage','POL','POT','POD','Depot','Empty Pickup Location','Release Date','Valid Until','Container Qty','Size / Type','Container Owner','Free Days','Special Instruction','CRO Status']:
        assert x in HTML,x
    assert "/api/clx070/container-control/booking-context/" in HTML
    assert "CRO Equipment Allocation" in HTML
    assert "Requested Qty" in HTML and "Allocated Count" in HTML

def test_release_controls_existing_record_only():
    for x in ['Container Release Control','Empty Release','Pickup Authorization','Payment Hold','Document Hold','Equipment Hold','Depot Release Status','Released By','Released Date','Authorized Override','Override Reason']:
        assert x in HTML,x

def test_crt_and_conditional_pot_logic():
    for x in ['CRT / Transshipment Master','CRT No.','Original Vessel / Voyage','POT','Next Vessel / Voyage','TS Agent','Discharge Date','Connection ETD','Connection ETA','Hold','Exception','Remarks']:
        assert x in HTML,x
    assert "POT Conditional Logic" in HTML
    assert "Direct shipment / no POT detected" in HTML
    assert "no transshipment data is forced" in HTML

def test_delivery_order_and_release_same_authoritative_record():
    for x in ['Delivery Order Master','DO No.','Delivery Party','Empty Return Location','Payment Status','Customs Status','Freight Status','Release Status']:
        assert x in HTML,x
    assert "Release Instruction — Same Authoritative Delivery Record" in HTML
    assert "Original B/L Status" in HTML and "Telex Release" in HTML and "Payment Block" in HTML

def test_governance_exceptions_links_and_preview():
    for x in ['Status Governance / Exception Control','Authoritative Document & Journey Links','Read-only ','Preview uses the currently selected authoritative transaction values']:
        assert x in HTML,x
    for target in ['agent-tasks::booking','agent-tasks::bl','agent-tasks::cro','agent-tasks::transshipment-trt','agent-tasks::delivery-order','agent-tasks::container-activity','gl-accounts::invoice','agent-tasks::soa','integration-security::request-response-audit']:
        assert target in HTML,target
    assert "clx83PreviewHtml" in HTML
    preview=HTML[HTML.index("function clx83PreviewHtml"):HTML.index("function clx83TogglePreview")]
    assert "data-clx42-field" not in preview and "data-clx49-field" not in preview

def test_acceptance_safety_flags_and_no_new_models():
    m=json.loads((ROOT/'CLX083_OPERATIONAL_FIELD_MATRIX.json').read_text())
    a=json.loads((ROOT/'CLX083_UI_ACCEPTANCE.json').read_text())
    assert m['baseline']=='8b047979a439cbda9686327e2ad41a329297ee7f'
    assert m['backend_model_changed'] is False and m['database_changed'] is False
    assert m['duplicate_operational_model_created'] is False
    assert a['backend_model_changed'] is False and a['database_changed'] is False
    assert a['REAL_MONEY_READY'] is False and a['REAL_PROVIDER_READY'] is False

def test_no_raw_json_operational_master():
    s=HTML.index("async function renderClx083OperationalMaster")
    e=HTML.index("async function renderInlineAgentScreen",s)
    block=HTML[s:e]
    assert "<pre class=" not in block
