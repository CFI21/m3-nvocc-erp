from pathlib import Path
import json,re

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

def booking_block():
    s=HTML.index("async function clx49RenderBooking(tab){")
    e=HTML.index("function clx49RenderSpecialRate",s)
    return HTML[s:e]

def test_required_basic_fields_present():
    b=booking_block()
    required=[
      "Booking No.","Approval #","Reference #","Booking Date","Carrier","Thru B/L","Container Owner","Sailing Date",
      "Commodity","Vessel","Voyage","POL","POL Agent","POD","POD Agent","POT (1)","TS Agent (1)","POT (2)","TS Agent (2)",
      "Shipper","Actual Shipper","Freight Type","Consignee","Notify Party","SRR"
    ]
    for x in required: assert x in b,x

def test_reference_is_explicit_review_later():
    b=booking_block()
    assert "Reference #',{review:true}" in b
    m=json.loads((ROOT/'CLX081_BOOKING_FIELD_MATRIX.json').read_text())
    ref=next(x for x in m['fields'] if x['reference']=='Reference #')
    assert ref['status']=='REVIEW_LATER' and ref['blocking'] is False

def test_two_leg_pot_ts_logic():
    assert "if(ts1)ts1.disabled=!pot1" in HTML
    assert "if(ts2)ts2.disabled=!pot2" in HTML
    b=booking_block()
    assert "'POT (1)'" in b and "'TS Agent (1)'" in b
    assert "'POT (2)'" in b and "'TS Agent (2)'" in b

def test_checkbox_save_is_governed_existing_booking_update():
    assert "el.type==='checkbox'?(el.checked?'true':'false'):el.value" in HTML
    assert "api('/api/v1/'+module+'/'+id" in HTML
    assert "Same as Booking Party" in HTML
    assert "clx81SameAsBookingParty" in HTML

def test_operational_control_and_existing_links():
    b=booking_block()
    for x in ["Organizations","Departure","Arrival","Docs","Pre-Allocation","Achieved Quantities","Numbers","Additional Detail"]:
        assert x in b
    for target in [
      "gl-accounts::profit-loss","integration-security::request-response-audit","agent-tasks::detention-collection",
      "integration-security::fx-rate-feed","agent-tasks::planning","agent-tasks::cro",
      "agent-tasks::transshipment-crt","agent-tasks::delivery-order"
    ]: assert target in HTML

def test_lower_workspace_and_commercial_flow():
    b=booking_block()
    for x in ["Equipment","Agreed Charges","Revenue","Cost","Financial Auto","Preview","Split Booking","B/L","CRO"]:
        assert x in b or x in HTML
    for x in ["Approved Rate Sources","Booking Context","Charge Ledger","Party Allocation","Principal / Agent SOA","Receipt / Settlement"]:
        assert x in b

def test_charge_grid_exact_column_contract():
    start=HTML.index("if(clx78BookingBottomTab==='Agreed Charges')")
    end=HTML.index("let rows=[]",start)
    b=HTML[start:end]
    cols=["Charge","Rate Source","Rule / Version","PP/CC","Collected By","Basis","Equipment","Qty","Currency","Sell / Full Local","Vendor Cost","Agent Cost","Principal Rec.","Principal Pay.","Principal Net","Customer AR"]
    for x in cols: assert x in b,x
    assert "/api/gl/job/" in HTML
    assert "/api/clx046/job/" in HTML
    assert "No parallel charge model" in HTML

def test_no_new_booking_model_or_schema():
    m=json.loads((ROOT/'CLX081_BOOKING_FIELD_MATRIX.json').read_text())
    a=json.loads((ROOT/'CLX081_UI_ACCEPTANCE.json').read_text())
    assert m['backend_model_changed'] is False
    assert m['duplicate_fields_created'] is False
    assert a['backend_model_changed'] is False
    assert a['database_changed'] is False

def test_safety():
    a=json.loads((ROOT/'CLX081_UI_ACCEPTANCE.json').read_text())
    assert a['REAL_MONEY_READY'] is False
    assert a['REAL_PROVIDER_READY'] is False
