from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

def home_block():
    s=HTML.index("async function showExecutiveHome(){")
    e=HTML.index("async function openBookingEquipmentAllocation",s)
    return HTML[s:e]

def test_clx085_authoritative_sources():
    b=home_block()
    for route in [
      "/api/v1/control-tower","/api/v1/operations-workbench","/api/v1/management-kpi/trends?days=30",
      "/api/v1/management-kpi/alerts?status=OPEN","/api/clx034/workbench?status=PENDING",
      "/api/clx070/container-control/kpis","/api/clx071/journey/kpis","/api/clx071/journey/exceptions",
      "/api/clx077/dummy-bank/safety","/api/clx011/screen-data?screen_id="
    ]: assert route in b,route

def test_executive_home_operational_financial_commercial_kpis():
    b=home_block()
    for x in ["Operational KPI","Bookings","Active Jobs","B/L","CRO","CRT / TS","Delivery Orders","Containers","Available Equipment","Gate In","Loaded","In Transit","Discharged","Empty Return","Overdue","Detention","Damage / Hold"]:
        assert x in b,x
    for x in ["Financial KPI","Revenue","Cost","Gross Profit","Margin","Customer AR","Vendor AP","Cash Received","Cash Paid","SOA Records","Finance Attention"]:
        assert x in b,x
    for x in ["Commercial KPI","Quote → Booking","Booking → B/L","Approved Rate Utilization","Sell vs Cost","REVIEW_LATER"]:
        assert x in b,x

def test_exception_approval_provider_audit_sections():
    b=home_block()
    for x in ["Exception Attention","Approval Attention","Management Control","Provider / Safety Status","Audit / Trace","Failed integration exceptions"]:
        assert x in b,x
    for target in ["integration-security::request-response-audit","master-data::approval-queue","integration-security::provider-adapters"]:
        assert target in b,target

def test_management_filters_no_parallel_master():
    b=HTML
    for x in ["Management Filters","Branch / Office","Agent","Principal","Customer","Trade Lane","POL","POD","Vessel","All Status"]:
        assert x in b,x
    assert "do not create parallel master data" in b.lower()

def test_data_consistency_and_no_manual_override():
    b=home_block()
    assert "Data Consistency / Source Governance" in b
    assert "No manual KPI override" in b
    assert "No manual KPI override, secondary audit log, KPI ledger, exception model or management summary table" in b

def test_matrix_and_acceptance_safety():
    m=json.loads((ROOT/'CLX085_KPI_SOURCE_MATRIX.json').read_text())
    a=json.loads((ROOT/'CLX085_UI_ACCEPTANCE.json').read_text())
    assert m['baseline']=='e6aec0503929783b22d835dcec0221013b5a43cd'
    assert m['backend_model_changed'] is False and m['database_changed'] is False
    assert m['new_management_model_created'] is False and m['new_kpi_ledger_created'] is False and m['new_exception_model_created'] is False
    assert m['no_manual_kpi_override'] is True
    assert a['REAL_MONEY_READY'] is False and a['REAL_PROVIDER_READY'] is False

def test_role_scope_and_no_raw_json_dashboard():
    b=home_block()
    assert "'X-Role':state.role" in b
    assert "<pre class=" not in b
