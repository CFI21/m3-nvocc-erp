import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.gl_seed import run as gl_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run
from app.screen_catalog import build_catalog
from app.clx044_gl_exact_flow import GL_SETUP_ORDER, GL_TRANSACTION_ORDER, FLOW, gl_flow
from app.gl import MODULES, validate, CreateBody, create

HTML=Path("web/index.html").read_text()

class FakeRequest:
    def __init__(self,payload): self.payload=payload
    async def body(self): return json.dumps(self.payload,sort_keys=True).encode()

@pytest.fixture()
def isolated_db(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'clx044.db')
    seed_run(True)
    gl_seed_run()
    masterdata_seed_run()
    admin_seed_run()
    return db.DB_PATH

def test_frozen_baseline_and_top_level_menu_preserved():
    c=build_catalog()
    assert c['screen_count']==196
    assert [x['domain'] for x in c['menu']]==[
      'Agent Tasks','HO Tasks','Treasury / AR-AP',
      'Integration & Security','General / Administration','Master Data'
    ]

def test_gl_exact_setup_transaction_reports_order():
    c=build_catalog()
    general=next(x for x in c['menu'] if x['domain']=='General / Administration')
    by_name={x['name']:x for x in general['submenus']}
    assert 'Setup' in by_name and 'Transaction' in by_name and 'Reports' in by_name
    setup=[x.split('::',1)[1] for x in by_name['Setup']['screens']]
    tx=[x.split('::',1)[1] for x in by_name['Transaction']['screens']]
    assert setup==GL_SETUP_ORDER
    assert tx==GL_TRANSACTION_ORDER
    names=[x['name'] for x in general['submenus']]
    assert names.index('Setup') < names.index('Transaction') < names.index('Reports')

def test_gl_module_keys_routes_and_screen_count_not_renamed():
    for key in GL_SETUP_ORDER+GL_TRANSACTION_ORDER:
        assert key in MODULES
        assert MODULES[key]['key']==key
        assert MODULES[key]['route'].startswith('/gl/')
    assert gl_flow()['screen_count_change']==0
    assert gl_flow()['module_keys_renamed'] is False

def test_reference_flow_metadata_matches_shared_screens():
    assert FLOW['chart-of-accounts']['tabs']==['Account Detail','Account Security']
    assert FLOW['voucher-properties']['tabs']==['CostCenter','Department','Location']
    assert FLOW['account-integration']['tabs'][0]=='Parent Account'
    assert FLOW['currency']['fields'][:6]==['Code','Name','Main Symbol','Sub Unit Symbol','Decimal Portion Digits','Default']
    assert FLOW['voucher']['tabs']==['Account Detail','Voucher Properties']
    assert FLOW['invoice']['tabs']==['Invoice Detail','Voucher Properties','Invoice Properties']
    assert FLOW['bills']['tabs']==['Invoice Detail','Voucher Properties','Invoice Properties']
    assert FLOW['receipt']['tabs']==['Account Detail','Invoice','Settlement','Voucher Properties','Account Properties']
    assert FLOW['payment']['tabs']==['Account Detail','Invoice','Settlement','Voucher Properties','Account Properties']
    assert FLOW['bank-reconciliation']['tabs']==['Record','Post Record']

def test_exact_reference_fields_exposed_by_gl_modules():
    for key,flow in FLOW.items():
        meta=MODULES[key]
        if 'fields' in flow: assert meta['fields']==flow['fields']
        if 'columns' in flow: assert meta['columns']==flow['columns']
        if 'tabs' in flow: assert meta['tabs']==flow['tabs']
        if 'line_columns' in flow: assert meta['line_columns']==flow['line_columns']

def test_reference_voucher_validation_balanced_and_unbalanced():
    good={'Account Lines':[
      {'Account Code':'1100','Debit (VC)':'100','Credit (VC)':'0'},
      {'Account Code':'4000','Debit (VC)':'0','Credit (VC)':'100'},
    ]}
    assert validate('voucher',good)==[]
    bad={'Account Lines':[
      {'Account Code':'1100','Debit (VC)':'100','Credit (VC)':'0'},
      {'Account Code':'4000','Debit (VC)':'0','Credit (VC)':'90'},
    ]}
    assert 'UNBALANCED_VOUCHER' in validate('voucher',bad)

def test_reference_invoice_and_reconciliation_rules():
    assert validate('invoice',{'Invoice Amount':'100','Tax Amount':'10','Net Amount':'110'})==[]
    assert 'INVOICE_NET_AMOUNT_MISMATCH' in validate('invoice',{'Invoice Amount':'100','Tax Amount':'10','Net Amount':'100'})
    assert validate('bank-reconciliation',{'Book Balance':'100','Statement Balance':'100','Difference':'0','Status':'Reconciled'})==[]
    assert 'RECONCILIATION_NOT_BALANCED' in validate('bank-reconciliation',{'Book Balance':'100','Statement Balance':'90','Difference':'10','Status':'Reconciled'})

def test_exact_line_grid_voucher_creates_existing_authoritative_voucher_engine(isolated_db):
    fields={
      'Voucher Type':'JV','Date':'2026-09-23','Currency':'USD','Exchange Rate':'1',
      'Account Lines':[
        {'Account Code':'1100','Particular':'Receipt','Debit (VC)':'100','Credit (VC)':'0','Narration':'CLX044'},
        {'Account Code':'4000','Particular':'Revenue','Debit (VC)':'0','Credit (VC)':'100','Narration':'CLX044'},
      ],
      'Status':'Draft'
    }
    body=CreateBody(external_ref='CLX044-VOUCHER-001',fields=fields)
    out=asyncio.run(create('voucher',body,FakeRequest(body.model_dump()),x_role='GL_ACCOUNTANT',x_m3_session=None,idempotency_key='CLX044-VOUCHER-001'))
    data=json.loads(out.body)
    c=db.connect()
    try:
        v=c.execute('SELECT * FROM gl_vouchers WHERE gl_record_id=?',(data['id'],)).fetchone()
        lines=list(c.execute('SELECT * FROM gl_voucher_lines WHERE voucher_id=? ORDER BY line_no',(v['id'],)))
        assert v['total_debit']==100 and v['total_credit']==100
        assert len(lines)==2
        assert lines[0]['account_code']=='1100' and lines[0]['debit']==100
        assert lines[1]['account_code']=='4000' and lines[1]['credit']==100
    finally:c.close()

def test_m3_ui_renders_reference_tabs_and_line_grid_without_old_visual_copy():
    assert "meta.tabs?.length" in HTML
    assert "meta.line_columns?.length" in HTML
    assert "data-clx44-line-row" in HTML
    assert "Reference actions:" in HTML
    assert "--nav:#4b5563" in HTML
