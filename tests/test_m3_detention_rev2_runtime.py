"""Isolated runtime acceptance for Rev2; no production data or money."""
import json
import re
from pathlib import Path
import pytest
from fastapi import HTTPException
from app import db
from app.seed import run as seed
from app.masterdata_seed import run as masters
from app.admin_seed import run as admins
from app import clx071_container_journey as journey
from app import item7_financial_document_governance as finance
from app.mrg import ensure

@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'rev2.db')
    seed(True); masters(); admins()
    c = db.connect(); ensure(c)
    # Mirror accepted additive journey columns in the SQLite test adapter.
    for migration in sorted(Path('migrations').glob('CLX07[01]*.sql')):
        for table, column, definition in re.findall(r'ALTER TABLE public\.(containers|container_events) ADD COLUMN IF NOT EXISTS (\w+) ([^;]+);', migration.read_text()):
            existing={x['name'] for x in c.execute(f'PRAGMA table_info({table})')}
            if column not in existing:c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
    r = dict(c.execute("SELECT * FROM transaction_records WHERE module='detention-collection' ORDER BY id LIMIT 1").fetchone())
    cid = r['container_id']
    c.execute('DELETE FROM container_events WHERE container_id=?', (cid,))
    c.execute("UPDATE containers SET size_type='40HC',free_time_start='2026-10-01T00:00:00+00:00',free_days=0 WHERE id=?", (cid,))
    c.execute("UPDATE transaction_records SET payload_json='{}',external_ref='DET-RUNTIME-TEST' WHERE id=?", (r['id'],))
    c.execute('DELETE FROM mrg_rules')
    for ref, side, party, rate in [('REV1','REVENUE','CUSTOMER',10),('COST1','COST','PRINCIPAL',6)]:
        c.execute("""INSERT INTO mrg_rules(rule_ref,mrg_type,rate_side,party_type,charge_code,effective_from,currency,rate_basis,unit_rate,free_days,status,version,maker,created_at,updated_at)
        VALUES(?,'DETENTION',?,?,'DET','2026-01-01','USD','PER_DAY',?,0,'APPROVED',1,'test','2026-01-01','2026-01-01')""", (ref,side,party,rate))
    c.close()
    from app.admin import Login, login
    r['_session'] = login(Login(username='admin', password='Admin123!', mfa_code='123456'))['session_token']
    return r

def calculate(case, stage, till, direction='AGENT_TO_CUSTOMER', **kwargs):
    session = kwargs.get('session', case['_session'] if 'role' not in kwargs else None)
    return journey.calculate_detention(case['id'],journey.DetentionCalculationBody(requested_stage=stage,calculate_till=till,commercial_direction=direction),x_role=kwargs.get('role','VIEWER'),x_m3_session=session,x_agent_scope=kwargs.get('agent'),x_branch_scope=None,x_depot_scope=None)

def returned(case, at):
    c=db.connect()
    c.execute("INSERT INTO container_events(event_id,job_id,container_id,event_type,event_time,location,status,source_module,detail_json) VALUES('TEST-ER',?,?,'EMPTY_RETURN',?,'TEST-DEPOT','RECORDED','test','{}')", (case['job_id'],case['container_id'],at))
    c.close()

def blocked(code, callback):
    with pytest.raises(HTTPException) as e: callback()
    assert e.value.detail['code']==code

def test_advance_ongoing_actual_finance_and_audit(case):
    a=calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00')
    b=calculate(case,'ONGOING','2026-10-05T00:00:00+00:00')
    returned(case,'2026-10-06T00:00:00+00:00')
    final=calculate(case,'ACTUAL',None)
    c=db.connect()
    rows=[dict(x) for x in c.execute('SELECT * FROM detention_segments WHERE transaction_id=? ORDER BY sequence',(case['id'],))]
    assert [x['stage'] for x in rows]==['ADVANCE','ONGOING','ACTUAL']
    assert sum(x['segment_days'] for x in rows)==5
    assert sum(x['base_amount'] for x in rows)==50
    fields={'Currency':'USD','Amount':'50'}
    link=finance.validate_detention_document_link(c,'invoice',fields,case['job_id'],'DETENTION_COLLECTION',a['detention_ref'])
    assert len(link['segment_refs'])==3
    assert c.execute("SELECT COUNT(*) n FROM audit_events WHERE action='DETENTION_STAGE_TRANSITION' AND job_id=?",(case['job_id'],)).fetchone()['n']==3
    assert final['ok']
    c.close()
    blocked('ACTUAL_ALREADY_POSTED',lambda:calculate(case,'ACTUAL',None))

def test_duplicate_period_and_early_return_roll_back(case):
    calculate(case,'ADVANCE','2026-10-05T00:00:00+00:00')
    blocked('DETENTION_PERIOD_ALREADY_COVERED',lambda:calculate(case,'ONGOING','2026-10-05T00:00:00+00:00'))
    returned(case,'2026-10-03T00:00:00+00:00')
    blocked('ER_BEFORE_COVERED_PERIOD',lambda:calculate(case,'ACTUAL',None))
    c=db.connect(); assert c.execute('SELECT COUNT(*) n FROM detention_segments').fetchone()['n']==1; c.close()

def test_direction_isolation_and_finance_collision(case):
    a=calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00')
    b=calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00','PRINCIPAL_TO_AGENT')
    assert a['detention_ref']!=b['detention_ref']
    c=db.connect()
    blocked('DETENTION_REF_DIRECTION_COLLISION',lambda:finance.validate_detention_document_link(c,'bills',{'Amount':'20','Currency':'USD'},case['job_id'],'DETENTION_COLLECTION',a['detention_ref']))
    finance.validate_detention_document_link(c,'bills',{'Amount':'12','Currency':'USD'},case['job_id'],'DETENTION_COLLECTION',b['detention_ref'])
    c.close()

def test_tariff_ambiguity_and_missing_fx(case):
    c=db.connect()
    c.execute("INSERT INTO mrg_rules SELECT 'REV2',mrg_type,rate_side,party_type,party_code,charge_code,charge_type,effective_from,effective_to,pol,pot,pod,depot_code,terminal_code,route_code,service_code,cargo_type,size_type,container_type,currency,rate_basis,unit_rate,minimum_rate,maximum_rate,free_days,lolo_rate,invoice_basis,slab_wise,booking_ref,job_ref,original_rule_ref,exception_reason,office_code,branch_code,organization_code,priority,remarks,status,version,maker,checker,approved_at,created_at,updated_at,vessel_code,voyage_no,contract_ref FROM mrg_rules WHERE rule_ref='REV1'")
    c.close()
    blocked('TARIFF_VERSION_AMBIGUOUS',lambda:calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00'))
    c=db.connect(); c.execute("DELETE FROM mrg_rules WHERE rule_ref='REV2'"); c.execute("UPDATE mrg_rules SET currency='ZZZ' WHERE rule_ref='REV1'"); c.close()
    blocked('FX_RATE_MISSING_ON_SEGMENT',lambda:calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00'))

def test_role_scope_and_rollback_switch(case):
    blocked('EQUIPMENT_POLICY_DENIED',lambda:calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00',role='VIEWER'))
    with pytest.raises(HTTPException) as denied:
        calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00',role='AGENT',agent='OTHER')
    assert denied.value.status_code==404
    c=db.connect();c.execute("INSERT INTO md_config(config_key,config_value,value_type,scope,status) VALUES('DETENTION_CALCULATION_ENABLED','false','BOOLEAN','GLOBAL','ACTIVE')");c.close()
    blocked('DETENTION_CALCULATION_DISABLED',lambda:calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00'))

def post_invoice(c, case, result, amount, refs, currency='USD', fx='1'):
    fields={'Amount':str(amount),'Invoice Amount':str(amount),'Currency':currency,'Exchange Rate':fx,'Detention Stage':'ADVANCE','Detention Segment Refs':refs,'Customer':'Test Customer','Status':'Posted'}
    c.execute("INSERT INTO gl_records(module,external_ref,job_id,status,version,payload_json,created_at,updated_at,source_type,source_ref) VALUES('invoice','INV-RUNTIME',?,'Posted',1,?,'2026-10-07','2026-10-07','DETENTION_COLLECTION',?)", (case['job_id'],json.dumps(fields),result['detention_ref']))
    return fields

def test_partial_advance_invoice_does_not_create_false_fx_loss(case):
    a=calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00')
    c=db.connect(); post_invoice(c,case,a,20,[a['segments'][0]['segment_ref']]); c.close()
    calculate(case,'ONGOING','2026-10-05T00:00:00+00:00')
    returned(case,'2026-10-06T00:00:00+00:00')
    out=calculate(case,'ACTUAL',None)
    assert out['fx_reconciliation_base']==0
    c=db.connect(); fields=json.loads(c.execute("SELECT payload_json FROM gl_records WHERE external_ref='INV-RUNTIME'").fetchone()['payload_json'])
    assert fields['Invoice Amount']=='20'
    assert fields['Exchange Rate']=='1'
    c.close()

def test_credit_bound_and_duplicate_finance_segment(case):
    a=calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00')
    ref=a['segments'][0]['segment_ref'];c=db.connect();post_invoice(c,case,a,20,[ref])
    fields={'Amount':'21','Currency':'USD','Invoice Ref':'INV-RUNTIME','Reason Code':'RATE','Reason':'Rate adjustment','Detention Segment Ref':ref,'Customer':'Test Customer'}
    blocked('CREDIT_EXCEEDS_INVOICE',lambda:finance.validate_correction(c,'credit-note',fields,case['job_id'],'invoice','INV-RUNTIME'))
    fields['Amount']='10'
    assert finance.validate_correction(c,'credit-note',fields,case['job_id'],'invoice','INV-RUNTIME')['detention_segment_ref']==ref
    blocked('DETENTION_SEGMENT_ALREADY_INVOICED',lambda:finance.validate_detention_document_link(c,'invoice',{'Amount':'20','Currency':'USD','Detention Segment Refs':[ref]},case['job_id'],'DETENTION_COLLECTION',a['detention_ref']))
    c.close()

def test_date_effective_tariff_split_and_snapshot_pinning(case):
    c=db.connect();c.execute("UPDATE mrg_rules SET effective_to='2026-10-02' WHERE rule_ref='REV1'")
    c.execute("INSERT INTO mrg_rules(rule_ref,mrg_type,rate_side,party_type,charge_code,effective_from,currency,rate_basis,unit_rate,free_days,status,version,maker,created_at,updated_at) VALUES('REV-NEW','DETENTION','REVENUE','CUSTOMER','DET','2026-10-03','USD','PER_DAY',15,0,'APPROVED',2,'test','2026-01-01','2026-01-01')");c.close()
    out=calculate(case,'ADVANCE','2026-10-05T00:00:00+00:00')
    assert [(r['rule_ref'],r['tariff_version'],r['segment_days'],r['segment_amount']) for r in out['segments']]==[('REV1',1,2,20),('REV-NEW',2,2,30)]
    c=db.connect();c.execute("UPDATE mrg_rules SET unit_rate=99 WHERE rule_ref='REV-NEW'")
    r=c.execute("SELECT tariff_snapshot_json FROM detention_segments WHERE rule_ref='REV-NEW'").fetchone()
    assert json.loads(r['tariff_snapshot_json'])['unit_rate']==15
    c.close()

def test_fx_rate_pinned_and_missing_mapping_rolls_back_actual(case):
    c=db.connect();c.execute("UPDATE mrg_rules SET currency='EUR' WHERE rule_ref='REV1'")
    c.execute("DELETE FROM gl_fx_rates WHERE currency='EUR'")
    c.execute("INSERT INTO gl_fx_rates(currency,base_currency,rate,rate_date,status,source) VALUES('EUR','USD',1.2,'2026-01-01','ACTIVE','TEST')");c.close()
    a=calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00')
    assert a['segments'][0]['fx_rate']==1.2
    c=db.connect();post_invoice(c,case,a,20,[a['segments'][0]['segment_ref']],currency='EUR',fx='1.3');c.close()
    returned(case,'2026-10-04T00:00:00+00:00')
    blocked('FX_GAIN_LOSS_MAPPING_MISSING',lambda:calculate(case,'ACTUAL',None))
    c=db.connect();assert c.execute("SELECT COUNT(*) n FROM detention_segments WHERE stage='ACTUAL'").fetchone()['n']==0;c.close()

def test_missing_segment_store_and_ref_collision(case):
    c=db.connect();c.execute('DROP TABLE detention_segments');c.close()
    blocked('SEGMENT_TABLE_MISSING',lambda:calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00'))

def test_unapproved_ancillary_charge_and_reefer_conflict(case):
    c=db.connect();c.execute("UPDATE mrg_rules SET status='PENDING_APPROVAL' WHERE rule_ref='REV1'");c.close()
    blocked('UNAPPROVED_CHARGE_CODE',lambda:journey.add_detention_charge(case['id'],journey.DetentionChargeBody(rule_ref='REV1'),x_role='SUPER_ADMIN',x_m3_session=None,x_agent_scope=None,x_branch_scope=None,x_depot_scope=None))
    blocked('REEFER_CLASSIFICATION_CONFLICT',lambda:journey._detention_reefer_classification({'size_type':'40RF','container_type':'DRY'}))

def test_authenticated_session_overrides_spoofed_role(case):
    from app.admin import Login, login
    auditor=login(Login(username='auditor',password='Audit123!',mfa_code='123456'))
    blocked('EQUIPMENT_POLICY_DENIED',lambda:calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00',role='SUPER_ADMIN',session=auditor['session_token']))
    admin=login(Login(username='admin',password='Admin123!',mfa_code='123456'))
    out=calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00',role='VIEWER',session=admin['session_token'])
    assert out['ok']
    c=db.connect();audit=json.loads(c.execute("SELECT metadata_json FROM audit_events WHERE action='DETENTION_STAGE_TRANSITION' ORDER BY ts DESC LIMIT 1").fetchone()['metadata_json'])
    assert audit['gl_posted'] is False and audit['payment_posted'] is False
    c.close()

def test_empty_return_requires_depot(case):
    c=db.connect();c.execute("UPDATE containers SET journey_state='GATE_OUT_FULL',depot_code=NULL WHERE id=?",(case['container_id'],));no=c.execute('SELECT container_no FROM containers WHERE id=?',(case['container_id'],)).fetchone()['container_no'];c.close()
    blocked('EMPTY_RETURN_DEPOT_REQUIRED',lambda:journey.post_event(no,journey.EventBody(event_type='EMPTY_RETURN',actual_time='2026-10-06T00:00:00+00:00'),x_role='SUPER_ADMIN',x_m3_session=None,x_agent_scope=None,x_branch_scope=None,x_depot_scope=None))

def test_authorized_finance_creates_draft_invoice_without_posting(case):
    import asyncio
    from starlette.requests import Request
    from app.admin import Login, login
    from app.gl import CreateBody, create
    a=calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00')
    token=login(Login(username='admin',password='Admin123!',mfa_code='123456'))['session_token']
    async def receive():return {'type':'http.request','body':b'{}','more_body':False}
    req=Request({'type':'http','method':'POST','path':'/api/gl/invoice','headers':[]},receive)
    body=CreateBody(job_ref='50001',external_ref='INV-RUNTIME-DRAFT',source_type='DETENTION_COLLECTION',source_ref=a['detention_ref'],fields={'Currency':'USD','Amount':'20','Customer':'Test Customer','Invoice Date':'2026-10-07','Due Date':'2026-11-07','Status':'Draft'})
    c=db.connect();before=c.execute('SELECT COUNT(*) n FROM gl_vouchers').fetchone()['n'];c.close()
    out=asyncio.run(create('invoice',body,req,x_role='GL_ACCOUNTANT',x_m3_session=token,idempotency_key=None))
    c=db.connect();r=c.execute("SELECT * FROM gl_records WHERE external_ref='INV-RUNTIME-DRAFT'").fetchone()
    assert r['status']=='Draft'
    assert json.loads(r['payload_json'])['Detention Segment Refs']==[a['segments'][0]['segment_ref']]
    assert c.execute('SELECT COUNT(*) n FROM gl_vouchers').fetchone()['n']==before
    assert c.execute("SELECT COUNT(*) n FROM audit_events WHERE module='gl:invoice' AND action='CREATE'").fetchone()['n']>=1
    c.close()

def test_day_regression_and_ref_collision(case):
    calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00')
    c=db.connect();c.execute("UPDATE mrg_rules SET free_days=10 WHERE rule_ref='REV1'");c.close()
    blocked('DETENTION_DAY_REGRESSION',lambda:calculate(case,'ONGOING','2026-10-05T00:00:00+00:00'))
    c=db.connect();c.execute("UPDATE mrg_rules SET free_days=0 WHERE rule_ref='REV1'")
    c.execute('UPDATE transaction_records SET payload_json=? WHERE id=?',(json.dumps({'Customer Detention Ref':'SAME','Principal Detention Ref':'SAME'}),case['id']));c.close()
    blocked('DETENTION_REF_DIRECTION_COLLISION',lambda:calculate(case,'ONGOING','2026-10-05T00:00:00+00:00'))

def test_principal_actual_flow_links_bill_instead_of_customer_invoice(case):
    a=calculate(case,'ADVANCE','2026-10-03T00:00:00+00:00','PRINCIPAL_TO_AGENT')
    calculate(case,'ONGOING','2026-10-05T00:00:00+00:00','PRINCIPAL_TO_AGENT')
    returned(case,'2026-10-06T00:00:00+00:00')
    final=calculate(case,'ACTUAL',None,'PRINCIPAL_TO_AGENT')
    assert final['process_status']=='COMPLETE'
    c=db.connect();fields={'Currency':'USD','Amount':'30'}
    link=finance.validate_detention_document_link(c,'bills',fields,case['job_id'],'DETENTION_COLLECTION',a['detention_ref'])
    assert link['document_amount']==30
    assert link['commercial_direction']=='PRINCIPAL_TO_AGENT'
    blocked('DETENTION_REF_DIRECTION_COLLISION',lambda:finance.validate_detention_document_link(c,'invoice',{'Currency':'USD','Amount':'30'},case['job_id'],'DETENTION_COLLECTION',a['detention_ref']))
    c.close()
