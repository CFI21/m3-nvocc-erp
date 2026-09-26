import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run
from app.screen_catalog import build_catalog, DOMAIN_SUBMENU_ORDER
from app.screen_integration import screen_data, quick_actions
from app.treasury import (
    META,READ_ONLY_MODULES,CreateBody,UpdateBody,ActionBody,
    create,update,action,list_records
)
from app.clx045_gl_reporting import gl_detail_rows
from app.clx046_treasury_hardening import workspace,job_trace,control_summary,verify

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'clx046.db')
    seed_run(True)
    masterdata_seed_run()
    admin_seed_run()
    return db.DB_PATH

def test_preserve_196_and_exact_treasury_submenu_order():
    c=build_catalog()
    assert c['screen_count']==196
    assert [x['domain'] for x in c['menu']]==[
        'Agent Tasks','HO Tasks','Treasury / AR-AP',
        'Integration & Security','General / Administration','Master Data'
    ]
    t=next(x for x in c['menu'] if x['domain']=='Treasury / AR-AP')
    assert [x['name'] for x in t['submenus']]==[
        'Overview','Setup','Cash / Bank','Settlement','Payments & Release',
        'Cheques','Work Queues','Reports & Reconciliation'
    ]
    assert DOMAIN_SUBMENU_ORDER['Treasury / AR-AP']['Overview'] < DOMAIN_SUBMENU_ORDER['Treasury / AR-AP']['Setup']

def test_all_existing_treasury_screens_load_and_keep_readonly_boundaries(isolated):
    c=build_catalog()
    screens=[x for x in c['screens'] if x['domain']=='Treasury / AR-AP']
    assert len(screens)==37
    for s in screens:
        d=screen_data(s['screen_id'],x_role='ADMIN')
        assert 'rows' in d
        a=set(quick_actions(s['screen_id'],'ADMIN')['visible_actions'])
        if s['key'] in READ_ONLY_MODULES or s['submenu'] in {'Overview','Work Queues','Reports & Reconciliation'}:
            assert not ({'create','edit','approve','release','reverse'} & a)

def test_readonly_modules_block_direct_mutation_api(isolated):
    key=next(iter(READ_ONLY_MODULES))
    with pytest.raises(HTTPException) as e:
        create(key,CreateBody(fields={'Reference':'X'}),None,'ADMIN','maker',None)
    assert e.value.status_code==405
    assert e.value.detail['code']=='TREASURY_READ_ONLY_SCREEN'

def test_idempotency_and_duplicate_payment_source_guard(isolated):
    body=CreateBody(job_ref='50001',external_ref='CLX046-PB-50001',fields={
        'Batch No.':'CLX046-PB-50001','Date':'2026-09-23','Payments':'CLX046-PAY-50001',
        'Currency':'USD','Total Amount':'3910','Maker':'maker-a','Status':'Draft'
    })
    a=create('payment-batches',body,'IDEM-CLX046-50001','FINANCE','maker-a',None)
    b=create('payment-batches',body,'IDEM-CLX046-50001','FINANCE','maker-a',None)
    assert a['id']==b['id']
    body2=CreateBody(job_ref='50001',external_ref='CLX046-PB-50001-DUP',fields={
        'Batch No.':'CLX046-PB-50001-DUP','Date':'2026-09-23','Payments':'CLX046-PAY-50001',
        'Currency':'USD','Total Amount':'3910','Maker':'maker-b','Status':'Draft'
    })
    with pytest.raises(HTTPException) as e:
        create('payment-batches',body2,None,'FINANCE','maker-b',None)
    assert e.value.status_code==409
    assert e.value.detail['code']=='DUPLICATE_TREASURY_SOURCE'

def test_maker_checker_release_posts_once_to_authoritative_gl_and_reports(isolated):
    c=db.connect()
    try:
        c.execute("INSERT OR REPLACE INTO gl_account_mappings(source_module,event_type,debit_account_code,credit_account_code,tax_account_code,active) VALUES('payment-batches','POST','2100','1100',NULL,1)")
        c.commit()
    finally:c.close()
    body=CreateBody(job_ref='50002',external_ref='CLX046-REL-50002',fields={
        'Batch No.':'CLX046-REL-50002','Date':'2026-09-23','Payments':'CLX046-SOURCE-50002',
        'Currency':'USD','Total Amount':'6120','Maker':'maker-a','Status':'Draft'
    })
    r=create('payment-batches',body,None,'FINANCE','maker-a',None)
    with pytest.raises(HTTPException) as same:
        action('payment-batches',r['id'],'approve',ActionBody(version=1),'FINANCE','maker-a',None)
    assert same.value.detail['code']=='MAKER_CANNOT_APPROVE_OWN_TRANSACTION'
    approved=action('payment-batches',r['id'],'approve',ActionBody(version=1),'FINANCE','checker-b',None)['record']
    released=action('payment-batches',r['id'],'release',ActionBody(version=approved['version']),'FINANCE','treasury-c',None)['record']
    assert released['status']=='Released'
    c=db.connect()
    try:
        links=list(c.execute("SELECT l.*,v.* FROM treasury_gl_links l JOIN gl_vouchers v ON v.id=l.voucher_id WHERE l.treasury_record_id=? AND l.link_type='POSTING'",(r['id'],)))
        assert len(links)==1
        v=links[0]
        assert v['status']=='Posted'
        assert v['total_debit']==6120 and v['total_credit']==6120
        assert v['base_total_debit']==v['base_total_credit']
        lines=list(c.execute("SELECT account_code,debit,credit FROM gl_voucher_lines WHERE voucher_id=? ORDER BY line_no",(v['voucher_id'],)))
        assert lines[0]['account_code']=='2100'
        assert lines[1]['account_code']=='1100'
    finally:c.close()
    detail=gl_detail_rows(status='Posted')
    assert any(x['Source Type']=='TREASURY' and x['Source Ref']=='CLX046-REL-50002' for x in detail)
    with pytest.raises(HTTPException) as second:
        action('payment-batches',r['id'],'release',ActionBody(version=released['version']),'FINANCE','treasury-c',None)
    assert second.value.detail['code']=='APPROVAL_REQUIRED_BEFORE_RELEASE'

def test_optimistic_lock_and_reversal(isolated):
    body=CreateBody(job_ref='50003',external_ref='CLX046-REV-50003',fields={
        'Refund Ref':'CLX046-REV-50003','Date':'2026-09-23','Customer':'Test Customer',
        'Receipt / Credit Ref':'RCPT-50003','Amount':'75','Currency':'USD','Status':'Draft'
    })
    r=create('customer-refunds',body,None,'FINANCE','maker-a',None)
    changed=update('customer-refunds',r['id'],UpdateBody(version=1,fields=body.fields|{'Reason':'validated'}),'FINANCE','maker-a',None)
    with pytest.raises(HTTPException) as stale:
        update('customer-refunds',r['id'],UpdateBody(version=1,fields=body.fields),'FINANCE','maker-a',None)
    assert stale.value.detail['code']=='OPTIMISTIC_LOCK_CONFLICT'
    approved=action('customer-refunds',r['id'],'approve',ActionBody(version=changed['version']),'FINANCE','checker-b',None)['record']
    released=action('customer-refunds',r['id'],'release',ActionBody(version=approved['version']),'FINANCE','treasury-c',None)['record']
    reversed_record=action('customer-refunds',r['id'],'reverse',ActionBody(version=released['version'],reason='CLX046 reversal UAT'),'FINANCE','checker-d',None)['record']
    assert reversed_record['status']=='Reversed'
    c=db.connect()
    try:
        assert c.execute("SELECT COUNT(*) n FROM treasury_reversals WHERE original_record_id=?",(r['id'],)).fetchone()['n']==1
        assert c.execute("SELECT COUNT(*) n FROM treasury_gl_links WHERE treasury_record_id=?",(r['id'],)).fetchone()['n']==2
    finally:c.close()

def test_reconciliation_controls(isolated):
    body=CreateBody(job_ref='50004',external_ref='CLX046-CASH-50004',fields={
        'Count Ref':'CLX046-CASH-50004','Date':'2026-09-23','Cash Account':'CASH-USD-RTM',
        'Currency':'USD','Counted Total':'3400','Book Balance':'3500','Difference':'-100','Counted By':'cashier','Status':'Open'
    })
    r=create('cash-denomination',body,None,'FINANCE','maker-a',None)
    with pytest.raises(HTTPException) as e:
        action('cash-denomination',r['id'],'reconcile',ActionBody(version=1),'FINANCE','checker-b',None)
    assert e.value.detail['code']=='CASH_DIFFERENCE_MUST_BE_ZERO'

def test_jobs_50001_50005_treasury_to_gl_reporting_trace(isolated):
    for i,jr in enumerate(('50001','50002','50003','50004','50005'),1):
        body=CreateBody(job_ref=jr,external_ref=f'CLX046-E2E-{jr}',fields={
            'Refund Ref':f'CLX046-E2E-{jr}','Date':'2026-09-23','Customer':f'Customer {jr}',
            'Receipt / Credit Ref':f'RCPT-{jr}-CLX046','Amount':str(20+i),'Currency':'USD','Status':'Draft'
        })
        r=create('customer-refunds',body,None,'FINANCE',f'maker-{jr}',None)
        a=action('customer-refunds',r['id'],'approve',ActionBody(version=1),'FINANCE',f'checker-{jr}',None)['record']
        action('customer-refunds',r['id'],'release',ActionBody(version=a['version']),'FINANCE',f'releaser-{jr}',None)
        t=job_trace(jr,'AUDITOR')
        assert t['trace_ok']
        assert any(x['treasury_ref']==f'CLX046-E2E-{jr}' for x in t['treasury_gl_links'])
        assert any(x['Source Type']=='TREASURY' and x['Source Ref']==f'CLX046-E2E-{jr}' for x in t['gl_report_lines'])
    v=verify('AUDITOR')
    assert v['all_jobs_present'] and v['all_have_treasury'] and v['all_reach_gl_reporting']

def test_control_summary_and_workspace(isolated):
    w=workspace('AUDITOR')
    assert [x['group'] for x in w['groups']]==[
        'overview','setup','cash-bank','settlement','payments','cheques','work-queues','reports'
    ]
    assert w['screen_count_change']==0
    s=control_summary('AUDITOR')
    assert not s['duplicate_gl_links']
    assert s['live_providers'] is False and s['real_money'] is False
