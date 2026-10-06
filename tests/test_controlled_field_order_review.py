from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

def booking_block():
    s=HTML.index("async function clx49RenderBooking(tab){")
    e=HTML.index("function clx49RenderSpecialRate",s)
    return HTML[s:e]

def test_screen_baseline_and_no_parallel_models():
    from app.screen_catalog import build_catalog
    c=build_catalog()
    assert c['screen_count']==196
    review=json.loads((ROOT/'M3_CONTROLLED_FIELD_ORDER_REVIEW.json').read_text())
    assert review['screen_count']==196
    assert review['database_changed'] is False
    assert review['api_contract_changed'] is False
    assert review['duplicate_model_created'] is False
    assert review['ancline_touched'] is False

def test_booking_groups_follow_operational_order():
    b=booking_block()
    ordered=[
        '<div class="clx81GroupTitle">BASIC</div>',
        '<div class="clx81GroupTitle">PARTIES</div>',
        '<div class="clx81GroupTitle">ROUTING</div>',
        '<div class="clx81GroupTitle">CARGO / EQUIPMENT</div>',
        'Operational Control',
        'Commercial Ownership / Financial Parties'
    ]
    pos=[b.index(x) for x in ordered]
    assert pos==sorted(pos)
    for field in [
        'Booking No.','Booking Date','Booking Status','Sales Person',
        'Customer / Booking Party','Shipper','Consignee','Notify Party','Billing Party',
        'Origin / Sending Agent','POT Agent','Destination / Receiving Agent',
        'Carrier / Shipping Line','Container Owner','Equipment Provider',
        'POL','POT (1)','TS Agent (1)','POD','Vessel','Voyage',
        'Commodity','Cargo Description','Packages','Gross Weight',
        'Measurement / Volume','Equipment Type','Quantity','SOC / COC'
    ]:
        assert field in b,field

def test_booking_duplicate_ui_fields_removed_from_operational_control():
    b=booking_block()
    op=b[b.index('Operational Control'):b.index('Commercial Ownership / Financial Parties')]
    assert "clx78Field(r,'Agent','Sending Agent')" not in op
    assert "clx78Field(r,'Receiving Agent','Receiving Agent')" not in op
    assert "clx78Field(r,'Carrier','Carrier')" not in op
    assert "Carrier Booking Ref" not in op
    for x in ['Operations Owner','SI Cut-off','VGM Cut-off','Gate-in Cut-off','Free Days POL','Free Days POD','Detention Tariff']:
        assert x in op

def test_pot_agent_is_conditioned_by_pot():
    b=booking_block()
    assert "clx78Field(r,'POT Agent','POT Agent',{disabled:!hasPot1})" in b
    assert "pot.disabled=!(via||trans)" in HTML
    assert "if(pot1)pot.disabled=false" in HTML
    assert "if(ts1)ts1.disabled=!pot1" in HTML
    assert "if(ts2)ts2.disabled=!pot2" in HTML

def test_existing_bl_gate_and_order_are_preserved():
    s=HTML.index("async function clx49RenderBlTab(tab){")
    e=HTML.index("function clx49BookingGroup",s)
    b=HTML[s:e]
    for x in [
      'B/L Basic Info & Routing','B/L No.','B/L Type','MBL','HBL','Booking No.','Job Ref',
      'Issue Date','Place of Issue','Original / Express','Freight Term','Service Type',
      'Carrier','Vessel','Voyage','POL','POT','POD','Final Destination',
      'Parties','Cargo & Equipment','Freight & Commercial Context'
    ]:
        assert x in b,x
    freeze=json.loads((ROOT/'M3_BACKEND_FREEZE_ACCEPTANCE.json').read_text())
    assert freeze['production_baseline_guard']['source_unclassified']==355

def test_cro_trt_release_do_keep_existing_authoritative_master():
    for x in [
      'CRO Basic Info / Routing','CRO No.','Equipment Allocation','Container Release Control',
      'TRT / Transshipment Master','POT Conditional Logic','Direct shipment / no POT detected',
      'Delivery Order Master','Release Instruction — Same Authoritative Delivery Record',
      'Empty Return Location','Payment Status','Customs Status','Freight Status','Release Status'
    ]:
        assert x in HTML,x

def test_container_and_finance_governance_preserved():
    for x in [
      'Global Equipment Tower','Container Master','Journey','Depot & M&R',
      'Job Costing Master — Existing Sources Only','No parallel costing table',
      'Manual rate / additional cost rule:','existing governed change / CRT / adjustment route',
      'No self-approval path is introduced by CLX-084.'
    ]:
        assert x in HTML,x

def test_real_money_and_providers_stay_off():
    review=json.loads((ROOT/'M3_CONTROLLED_FIELD_ORDER_REVIEW.json').read_text())
    assert review['real_money'] is False
    assert review['dummy_bank']=='SIMULATION_ONLY'
    assert review['providers']=={'TRUE_LAYER':False,'OXR':False,'AVALARA':False}
