import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.screen_catalog import build_catalog
from app.main import UpdateBody,update_record
from app.clx049_booking_bl_workspace import workspace,trace,verify,BL_TABS,BOOKING_TABS

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'clx049.db')
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH

def test_frozen_196_and_menu_order_preserved(isolated):
    c=build_catalog()
    assert c['screen_count']==196
    assert [x['domain'] for x in c['menu']]==[
      'HO Tasks','Agent Tasks','Treasury / AR-AP',
      'Integration & Security','General / Administration','Master Data'
    ]
    for key in ('special-rates-request','booking','bl','delivery-order','vessel-lock','switch-bl'):
        s=next(x for x in c['screens'] if x['screen_id']=='agent-tasks::'+key)
        assert s['domain']=='Agent Tasks'

def test_exact_booking_and_bl_same_page_tab_order(isolated):
    assert BOOKING_TABS==['Booking Info','Equipment','Other Info']
    assert BL_TABS==['Booking Info','Release Instruction','Delivery Order','Lock Info','Authorization']
    w=workspace('50001','AUDITOR',None)
    assert w['booking_tabs']==BOOKING_TABS
    assert w['bl_tabs']==BL_TABS

def test_workspace_reuses_authoritative_records_only(isolated):
    c=db.connect()
    try:
        before={m:c.execute("SELECT COUNT(*) n FROM transaction_records WHERE module=?",(m,)).fetchone()['n']
                for m in ('special-rates-request','booking','bl','delivery-order','vessel-lock','switch-bl')}
    finally:c.close()
    w=workspace('50001','AUDITOR',None)
    assert w['parallel_records_created'] is False
    assert w['booking']['module']=='booking'
    assert w['bl']['module']=='bl'
    assert w['delivery_order']['module']=='delivery-order'
    assert w['lock_info']['vessel_lock']['module']=='vessel-lock'
    assert w['switch_bl']['module']=='switch-bl'
    c=db.connect()
    try:
        after={m:c.execute("SELECT COUNT(*) n FROM transaction_records WHERE module=?",(m,)).fetchone()['n'] for m in before}
    finally:c.close()
    assert before==after

def test_delivery_order_and_release_instruction_save_same_existing_do_record(isolated):
    w=workspace('50001','OPS',None)
    r=w['delivery_order'];rid=r['id'];v=r['version']
    out=update_record('delivery-order',rid,UpdateBody(version=v,fields={
      'Release Instruction':'Release to approved consignee only',
      'Valid Until':'2026-10-31'
    }),x_role='OPS',x_agent_scope=None,x_customer_scope=None)
    assert out['id']==rid
    assert out['version']==v+1
    w2=workspace('50001','OPS',None)
    assert w2['delivery_order']['id']==rid
    assert w2['delivery_order']['fields']['Release Instruction']=='Release to approved consignee only'

def test_booking_other_info_updates_same_booking_transaction(isolated):
    w=workspace('50002','OPS',None)
    r=w['booking'];rid=r['id'];v=r['version']
    out=update_record('booking',rid,UpdateBody(version=v,fields={
      'Freight Term':'PREPAID',
      'Rate / Quote Ref':'Q-50002-CLX049'
    }),x_role='OPS',x_agent_scope=None,x_customer_scope=None)
    assert out['id']==rid
    w2=workspace('50002','OPS',None)
    assert w2['booking']['id']==rid
    assert w2['booking']['fields']['Rate / Quote Ref']=='Q-50002-CLX049'

def test_bl_authorization_uses_same_bl_and_existing_audit(isolated):
    w=workspace('50003','DOCS',None)
    bl=w['bl']
    out=update_record('bl',bl['id'],UpdateBody(version=bl['version'],fields={'Draft Approval':'Pending Checker'}),x_role='DOCS',x_agent_scope=None,x_customer_scope=None)
    w2=workspace('50003','AUDITOR',None)
    assert w2['bl']['id']==bl['id']
    assert w2['authorization']['draft_approval']=='Pending Checker'
    assert any(x['action']=='UPDATE' and x['module']=='bl' for x in w2['authorization']['audit'])

def test_agent_scope_enforced_on_same_page_workspace(isolated):
    w=workspace('50001','AUDITOR',None)
    correct=w['context']['agent_code']
    assert workspace('50001','AGENT',correct)['context']['job_ref']=='50001'
    with pytest.raises(HTTPException):
        workspace('50001','AGENT','WRONG-AGENT')

def test_deep_links_are_exact_existing_screens(isolated):
    w=workspace('50001','AUDITOR',None)
    assert w['deep_links']=={
      'special_rate_request':'agent-tasks::special-rates-request',
      'booking':'agent-tasks::booking',
      'bl':'agent-tasks::bl',
      'delivery_order':'agent-tasks::delivery-order',
      'lock_info':'agent-tasks::vessel-lock',
      'switch_bl':'agent-tasks::switch-bl'
    }

def test_jobs_50001_50005_full_reference_chain(isolated):
    for jr in ('50001','50002','50003','50004','50005'):
        t=trace(jr,'AUDITOR',None)
        assert t['pass'] is True, (jr,t['checks'])
        assert all(t['checks'].values())
        assert t['refs']['booking']
        assert t['refs']['bl']
        assert t['refs']['delivery_order']
        assert t['refs']['vessel_lock']
        assert t['refs']['switch_bl']
    v=verify('AUDITOR')
    assert v['all_jobs_pass'] is True
    assert v['screen_count_change']==0
    assert v['data_model_change'] is False
    assert v['parallel_records_created'] is False

def test_switch_bl_preserves_source_relationship_and_history(isolated):
    for jr in ('50001','50002','50003','50004','50005'):
        w=workspace(jr,'AUDITOR',None)
        sw=w['switch_bl']
        assert sw
        assert sw['fields']['Original B/L']==w['context']['hbl_no']
        if sw['fields'].get('Approval')=='Approved':
            assert w['switch_bl_history']

def test_web_contains_exact_same_page_workspace_contract():
    html=open('web/index.html',encoding='utf-8').read()
    assert "const CLX49_BL_TABS=['Booking Info','Release Instruction','Delivery Order','Lock Info','Authorization']" in html
    assert "const CLX49_BOOKING_TABS=['Booking Info','Equipment','Other Info']" in html
    assert "showClx049Workspace" in html
    assert "saveClx049Tab" in html
    assert "openClx49Link" in html
    assert "/api/clx049/workspace/" in html
    assert "/api/v1/'+module+'/'+id" in html
    assert "Approve via Existing B/L Governance" in html
    assert "--nav:#4b5563" in html
