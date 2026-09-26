import asyncio, hashlib, hmac, json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run
from app.screen_catalog import build_catalog
from app.screen_integration import screen_data,quick_actions
from app.integration import (
    ProviderSimulation,RetryBody,ManualMatchBody,PaymentAction,
    provider_state,simulate_provider,retry_event,webhook,manual_match,
    payments,payment_release,payment_gates,MOCK_WEBHOOK_SECRET
)
from app.clx047_integration_hardening import workspace,job_trace,control_summary,verify

class FakeRequest:
    def __init__(self,body=b'',headers=None):
        self._body=body
        self.headers=headers or {}
    async def body(self): return self._body

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'clx047.db')
    seed_run(True)
    masterdata_seed_run()
    admin_seed_run()
    return db.DB_PATH

def test_preserve_196_and_exact_integration_menu_order(isolated):
    c=build_catalog()
    assert c['screen_count']==196
    assert [x['domain'] for x in c['menu']]==[
      'Agent Tasks','HO Tasks','Treasury / AR-AP',
      'Integration & Security','General / Administration','Master Data'
    ]
    d=next(x for x in c['menu'] if x['domain']=='Integration & Security')
    assert [x['name'] for x in d['submenus']]==[
      'Overview','Providers','Sandbox Flows','Event Control','Reconciliation','Security / Control'
    ]
    assert sum(len(x['screens']) for x in d['submenus'])==20

def test_all_integration_screens_load_and_ui_stays_readonly(isolated):
    c=build_catalog()
    screens=[x for x in c['screens'] if x['domain']=='Integration & Security']
    assert len(screens)==20
    for s in screens:
        d=screen_data(s['screen_id'],x_role='ADMIN')
        assert 'rows' in d
        actions=set(quick_actions(s['screen_id'],'ADMIN')['visible_actions'])
        assert not ({'create','edit','approve','release','reverse','cancel','amend'} & actions)

def test_provider_state_is_mock_only_and_credentials_redacted(isolated):
    rows=provider_state('AUDITOR')
    assert rows
    assert all(r['endpoint_mode']=='MOCK' for r in rows)
    assert all(r['credential_ref']=='secret://sandbox/[REDACTED]' for r in rows)

def test_simulation_blocks_non_mock_provider(isolated):
    c=db.connect()
    try:
        c.execute("UPDATE finance_providers SET endpoint_mode='LIVE' WHERE provider_key='mock-bank'")
        c.commit()
    finally:c.close()
    body=ProviderSimulation(event_type='BANK_TEST',mode='success',payload={'token':'secret-value'})
    raw=json.dumps(body.model_dump(),sort_keys=True).encode()
    req=FakeRequest(raw,{'origin':'http://m3.test'})
    with pytest.raises(HTTPException) as e:
        asyncio.run(simulate_provider('mock-bank',body,req,'ADMIN','admin','sandbox-csrf','CLX047-MOCK-GATE'))
    assert e.value.status_code==403
    assert e.value.detail['code']=='LIVE_PROVIDER_DISABLED'

def test_provider_idempotency_and_secret_redaction(isolated):
    body=ProviderSimulation(event_type='PAYMENT_TEST',mode='success',payload={'token':'very-secret','iban':'NL00TEST1234','amount':999})
    raw=json.dumps(body.model_dump(),sort_keys=True).encode()
    req=FakeRequest(raw,{'origin':'http://m3.test'})
    a=asyncio.run(simulate_provider('mock-payments',body,req,'ADMIN','admin','sandbox-csrf','IDEM-CLX047-1'))
    b=asyncio.run(simulate_provider('mock-payments',body,req,'ADMIN','admin','sandbox-csrf','IDEM-CLX047-1'))
    ad=json.loads(a.body) if hasattr(a,'body') else a
    assert ad['id']==b['id']
    c=db.connect()
    try:
        r=c.execute("SELECT request_redacted,correlation_id FROM integration_events WHERE idempotency_key='IDEM-CLX047-1'").fetchone()
        assert 'very-secret' not in r['request_redacted']
        assert '[REDACTED]' in r['request_redacted']
        assert r['correlation_id']
    finally:c.close()

def test_retry_limit_and_open_circuit_are_enforced(isolated):
    c=db.connect()
    try:
        dead=c.execute("SELECT id FROM integration_events WHERE status='DEAD_LETTER' ORDER BY id LIMIT 1").fetchone()['id']
        retry=c.execute("SELECT e.id,e.provider_id FROM integration_events e WHERE e.status='RETRY' ORDER BY e.id LIMIT 1").fetchone()
        c.execute("UPDATE finance_providers SET circuit_state='OPEN' WHERE id=?",(retry['provider_id'],))
        c.commit()
    finally:c.close()
    req=FakeRequest(headers={'origin':'http://m3.test'})
    with pytest.raises(HTTPException) as a:
        retry_event(dead,RetryBody(force_success=False),req,'ADMIN','admin','sandbox-csrf')
    assert a.value.detail['code']=='RETRY_LIMIT_EXCEEDED'
    with pytest.raises(HTTPException) as b:
        retry_event(retry['id'],RetryBody(force_success=True),req,'ADMIN','admin','sandbox-csrf')
    assert b.value.detail['code']=='CIRCUIT_OPEN'

def test_webhook_duplicate_payload_conflict_and_signature_validation(isolated):
    raw=b'{"event":"ok","job":"50001"}'
    sig=hmac.new(MOCK_WEBHOOK_SECRET,raw,hashlib.sha256).hexdigest()
    req=FakeRequest(raw)
    a=asyncio.run(webhook('mock-payments',req,sig,'WH-CLX047-001'))
    assert a['accepted'] is True and a['sandbox_only'] is True
    dup=asyncio.run(webhook('mock-payments',req,sig,'WH-CLX047-001'))
    assert dup['duplicate'] is True
    raw2=b'{"event":"different","job":"50001"}'
    sig2=hmac.new(MOCK_WEBHOOK_SECRET,raw2,hashlib.sha256).hexdigest()
    with pytest.raises(HTTPException) as e:
        asyncio.run(webhook('mock-payments',FakeRequest(raw2),sig2,'WH-CLX047-001'))
    assert e.value.status_code==409
    assert e.value.detail['code']=='WEBHOOK_EVENT_ID_PAYLOAD_MISMATCH'

def test_manual_bank_match_requires_authoritative_source_and_prevents_rematch(isolated):
    c=db.connect()
    try:
        line=c.execute("SELECT id FROM bank_import_lines WHERE status='UNMATCHED' ORDER BY id LIMIT 1").fetchone()['id']
    finally:c.close()
    req=FakeRequest(headers={'origin':'http://m3.test'})
    with pytest.raises(HTTPException) as bad:
        manual_match(line,ManualMatchBody(source_ref='NOT-A-REAL-SOURCE'),req,'FINANCE','fin','sandbox-csrf')
    assert bad.value.detail['code']=='UNKNOWN_RECONCILIATION_SOURCE'
    ok=manual_match(line,ManualMatchBody(source_ref='RCPT-50001'),req,'FINANCE','fin','sandbox-csrf')
    assert ok['ok'] is True
    with pytest.raises(HTTPException) as again:
        manual_match(line,ManualMatchBody(source_ref='RCPT-50001'),req,'FINANCE','fin','sandbox-csrf')
    assert again.value.detail['code']=='BANK_LINE_ALREADY_MATCHED'

def test_security_audit_is_immutable(isolated):
    c=db.connect()
    try:
        row=c.execute('SELECT id FROM security_audit_events ORDER BY id LIMIT 1').fetchone()
        with pytest.raises(Exception):
            c.execute("UPDATE security_audit_events SET outcome='TAMPERED' WHERE id=?",(row['id'],))
    finally:c.close()

def test_payment_safety_and_simulated_release_only(isolated):
    rows=payments('AUDITOR')
    p1=next(r for r in rows if r['job_ref']=='50001')
    p2=next(r for r in rows if r['job_ref']=='50002')
    p4=next(r for r in rows if r['job_ref']=='50004')
    assert p1['gates']==[]
    assert 'BENEFICIARY_NOT_VALIDATED' in p2['gates']
    assert 'GL_NOT_BALANCED' in p4['gates']
    req=FakeRequest(headers={'origin':'http://m3.test'})
    out=payment_release(p1['id'],PaymentAction(version=p1['version']),req,'TREASURY_MANAGER','treasury-release-1','sandbox-session-treasury','sandbox-reauth-ok','sandbox-csrf')
    assert out['execution_mode']=='SIMULATED'
    assert out['real_money_movement'] is False
    c=db.connect()
    try:
        r=c.execute('SELECT release_mode FROM sandbox_payment_releases WHERE payment_request_id=?',(p1['id'],)).fetchone()
        assert r['release_mode']=='SIMULATED'
    finally:c.close()

def test_jobs_50001_50005_integration_to_treasury_gl_reporting_trace(isolated):
    for jr in ('50001','50002','50003','50004','50005'):
        t=job_trace(jr,'AUDITOR')
        assert len(t['integration_records'])==20
        assert t['treasury_records']
        assert t['gl_report_lines']
        assert t['trace_ok'] is True
        assert t['sandbox_only'] is True
    v=verify('AUDITOR')
    assert v['all_jobs_present']
    assert v['all_have_integration']
    assert v['all_reach_treasury']
    assert v['all_reach_gl_reporting']
    assert v['providers_mock_only']
    assert v['retry_limits_respected']
    assert v['releases_simulated_only']

def test_workspace_and_control_summary(isolated):
    w=workspace('AUDITOR')
    assert [x['group'] for x in w['groups']]==[
      'OVERVIEW','PROVIDERS','SANDBOX FLOWS','EVENT CONTROL','RECONCILIATION','SECURITY / CONTROL'
    ]
    assert w['module_count']==20 and w['screen_count_change']==0
    s=control_summary('AUDITOR')
    assert s['all_providers_mock']
    assert s['all_credentials_redacted']
    assert s['all_events_correlated']
    assert s['retry_limits_respected']
    assert s['live_providers'] is False and s['real_money'] is False
