import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.screen_catalog import build_catalog, AGENT_SETUP_ALIASES
from app.clx042_agent_workspace import form_schema, trace, verify, MODULES
from app.main import CreateBody, UpdateBody, create_record, update_record, list_records

HTML=Path("web/index.html").read_text()

class FakeRequest:
    def __init__(self,payload): self.payload=payload
    async def body(self): return json.dumps(self.payload,sort_keys=True).encode()

@pytest.fixture()
def isolated_db(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'clx042.db')
    seed_run(True)
    masterdata_seed_run()
    return db.DB_PATH

def test_agent_workspace_preserves_frozen_clx041_navigation():
    c=build_catalog()
    assert c['screen_count']==196
    assert [x['domain'] for x in c['menu']]==[
        'Agent Tasks','HO Tasks','Treasury / AR-AP',
        'Integration & Security','General / Administration','Master Data'
    ]
    agent=c['menu'][0]
    assert [x['name'] for x in agent['submenus']]==['Setup','Transaction']
    assert [x['label'] for x in agent['submenus'][0]['items']]==[
        'Agent','Common Parties','Vessel','Voyage Registration','Commodity','Units','Sales Person'
    ]
    assert len(agent['submenus'][1]['screens'])==19

def test_all_setup_and_transaction_forms_resolve_to_authoritative_apis():
    for item in AGENT_SETUP_ALIASES:
        s=form_schema(item['target'],'create')
        assert s['kind']=='AGENT_SETUP_MASTER'
        assert s['change_api']=='/api/masterdata/changes'
        assert s['maker_checker'] is True
        assert s['parallel_data_store'] is False
    for key in MODULES:
        s=form_schema('agent-tasks::'+key,'create')
        assert s['kind']=='AGENT_TRANSACTION'
        assert s['create_api']=='/api/v1/'+key
        assert s['edit_api_template']=='/api/v1/'+key+'/{id}'
        assert s['parallel_data_store'] is False

def test_ui_uses_real_drawer_forms_and_governed_setup_changes():
    for token in (
        "openAgentForm('create')","openAgentForm('edit')","saveAgentForm(mode)",
        "/api/clx042/form-schema","schema.create_api","schema.edit_api_template",
        "schema.change_api","X-Agent-Scope","Idempotency-Key",
        "CLX-042 governed Agent Setup form","executeAgentAction(a)"
    ):
        assert token in HTML

def test_agent_role_requires_agent_scope(isolated_db):
    with pytest.raises(HTTPException) as exc:
        list_records('crt',x_role='AGENT',x_agent_scope=None,x_customer_scope=None)
    assert exc.value.status_code==403
    assert 'X-Agent-Scope' in str(exc.value.detail)

def test_transaction_create_edit_duplicate_and_audit_use_existing_api(isolated_db):
    body=CreateBody(job_ref='50001',external_ref='CLX42-CRT-50001',fields={'Job Ref':'50001','Status':'Draft'})
    request=FakeRequest(body.model_dump())
    created=asyncio.run(create_record('crt',body,request,x_role='OPS',idempotency_key=None,x_agent_scope=None,x_customer_scope=None))
    data=json.loads(created.body)
    assert data['external_ref']=='CLX42-CRT-50001'
    assert data['status']=='Draft'
    tid=data['id']; version=data['version']

    edited=update_record('crt',tid,UpdateBody(version=version,fields={'Status':'Open','Remarks':'CLX-042 edit'}),x_role='OPS',x_agent_scope=None,x_customer_scope=None)
    assert edited['status']=='Open'
    assert edited['fields']['Remarks']=='CLX-042 edit'

    with pytest.raises(HTTPException) as exc:
        asyncio.run(create_record('crt',body,FakeRequest(body.model_dump()),x_role='OPS',idempotency_key=None,x_agent_scope=None,x_customer_scope=None))
    assert exc.value.status_code==409
    assert 'DUPLICATE' in str(exc.value.detail)

    c=db.connect()
    try:
        actions=[r['action'] for r in c.execute("SELECT action FROM audit_events WHERE transaction_id=? ORDER BY id",(tid,))]
        assert actions==['CREATE','UPDATE']
    finally:c.close()

def test_agent_scope_blocks_other_agent_job(isolated_db):
    rows=list_records('crt',x_role='AGENT',x_agent_scope='DOES-NOT-MATCH',x_customer_scope=None)
    assert rows['count']==0

def test_50001_50005_agent_trace_is_complete(isolated_db):
    for jr in ('50001','50002','50003','50004','50005'):
        t=trace(jr)
        assert t['complete'] is True
        assert t['agent_transactions']==19
        assert t['expected_modules']==19
        assert t['missing_modules']==[]
        assert t['broken_core_links']==0
        assert t['duplicate_module_refs']==0
        assert t['related']['containers']>=1
        assert t['related']['bills']>=2
        assert t['related']['gl']>=1
        assert t['related']['treasury']>=1

def test_clx042_verification_invariants():
    v=verify()
    assert v['status']=='PASS'
    assert v['screen_count']==196
    assert v['agent_transaction_screens']==19
    assert v['agent_modules']==19
    assert v['menu_reordered'] is False
    assert v['data_model_changed'] is False
    assert v['live_providers'] is False
    assert v['real_money'] is False
