from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

def test_clx084_scope_and_authoritative_sources():
    assert "const CLX84_FINANCE_KEYS=new Set(['invoice','bills','soa'])" in HTML
    assert "/api/gl/job/" in HTML and "/api/clx046/job/" in HTML
    assert "renderClx084FinanceMaster" in HTML

def test_invoice_master_fields():
    for x in ['Customer Invoice Master','Invoice No.','Job Ref','Booking','B/L','Customer','Billing Party','Invoice Date','Due Date','Currency','FX Rate','Invoice Charge Lines','Subtotal','Tax','Total','Paid','Outstanding','Credit Note','Status','Remarks']:
        assert x in HTML,x
    assert "openFinanceForm('edit')" in HTML

def test_bill_ap_master_fields():
    for x in ['Vendor Bill / AP Master','Bill No.','Vendor / Supplier','Carrier','Agent','Bill Date','Dispute','Hold','Bill Charge Lines']:
        assert x in HTML,x

def test_job_costing_revenue_cost_and_pnl():
    for x in ['Job Costing Master — Existing Sources Only','Charge Code','Rate Source','Basis','Qty','Equipment','Sell Rate','Cost Rate','Revenue Control','Cost Control','Agent / Principal Allocation','Job P&L — Read-only','Gross Profit','Margin','Cash Received','Cash Paid']:
        assert x in HTML,x
    assert "No parallel costing table" in HTML

def test_soa_master_and_treasury_gl_sources():
    for x in ['SOA Master','Party Type','Period','Opening Balance','Debit','Credit','Invoices','Bills','Receipts','Payments','Adjustments','FX Difference','Closing Balance','Unallocated Items','Disputed Items','SOA Linked Treasury / GL Activity']:
        assert x in HTML,x

def test_payment_links_and_governance():
    for target in ['agent-tasks::booking','agent-tasks::bl','agent-tasks::cro','agent-tasks::transshipment-trt','agent-tasks::delivery-order','gl-accounts::invoice','gl-accounts::bills','gl-accounts::receipt','gl-accounts::payment','gl-accounts::voucher','agent-tasks::soa','integration-security::request-response-audit']:
        assert target in HTML,target
    assert 'Manual rate / additional cost rule:' in HTML
    assert 'existing governed change / CRT / adjustment route' in HTML
    assert 'Four-eyes:' in HTML
    assert 'No self-approval path is introduced by CLX-084.' in HTML

def test_preview_is_read_only_same_dataset():
    s=HTML.index("function clx84PreviewHtml")
    e=HTML.index("function clx84TogglePreview",s)
    p=HTML[s:e]
    assert "Read-only " in p
    assert "No second financial dataset is created." in p
    assert "data-clx43-field" not in p and "data-clx42-field" not in p

def test_acceptance_safety_no_new_models():
    m=json.loads((ROOT/'CLX084_FINANCE_FIELD_MATRIX.json').read_text())
    a=json.loads((ROOT/'CLX084_SOA_UI_ACCEPTANCE.json').read_text())
    assert m['baseline']=='d97ff08e67d107768d3d4d8f7699f0e5c57f45f9'
    assert m['backend_model_changed'] is False and m['database_changed'] is False and m['duplicate_finance_model_created'] is False
    assert a['backend_model_changed'] is False and a['database_changed'] is False
    assert a['REAL_MONEY_READY'] is False and a['REAL_PROVIDER_READY'] is False

def test_no_raw_json_in_finance_master():
    s=HTML.index("async function renderClx084FinanceMaster")
    e=HTML.index("function financeDomain()",s)
    block=HTML[s:e]
    assert "<pre class=" not in block
