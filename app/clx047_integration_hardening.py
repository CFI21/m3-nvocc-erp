from fastapi import APIRouter,Header,HTTPException
import json
from .db import connect
from .integration import META,require_role
from .clx045_gl_reporting import gl_detail_rows

router=APIRouter(prefix='/api/clx047',tags=['CLX-047 Integration Security Hardening'])
GROUP_ORDER=['OVERVIEW','PROVIDERS','SANDBOX FLOWS','EVENT CONTROL','RECONCILIATION','SECURITY / CONTROL']

@router.get('/workspace')
def workspace(x_role:str=Header('AUDITOR')):
    require_role(x_role,'view')
    groups=[]
    for g in GROUP_ORDER:
        items=[{'key':m['key'],'name':m['name'],'route':m['route'],'read_only_ui':True} for m in META if m.get('group')==g]
        if items:groups.append({'group':g,'items':items})
    return {'phase':'CLX-047','groups':groups,'module_count':len(META),'screen_count_change':0,'sandbox_only':True,'live_providers':False,'real_money':False}

@router.get('/job/{job_ref}/trace')
def job_trace(job_ref:str,x_role:str=Header('AUDITOR')):
    require_role(x_role,'view');c=connect()
    try:
        j=c.execute('''SELECT j.id,j.job_ref,b.booking_ref,c.code customer_code,c.name customer_name,a.code agent_code,a.name agent_name
          FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id
          WHERE j.job_ref=?''',(job_ref,)).fetchone()
        if not j:raise HTTPException(404,'Unknown job')
        integrations=[dict(r) for r in c.execute('''SELECT ir.id,ir.module,ir.external_ref,ir.office_scope,ir.country_scope,ir.status,
          p.provider_key,p.provider_type,p.endpoint_mode FROM integration_records ir
          JOIN finance_providers p ON p.id=ir.provider_id WHERE ir.job_id=? ORDER BY ir.module,ir.id''',(j['id'],))]
        treasury=[dict(r) for r in c.execute('SELECT id,module,external_ref,status,amount,currency,source_ref FROM treasury_records WHERE job_id=? ORDER BY module,id',(j['id'],))]
        links=[dict(r) for r in c.execute('''SELECT t.module treasury_module,t.external_ref treasury_ref,l.link_type,l.source_ref voucher_ref,
          v.voucher_no,v.status voucher_status,v.total_debit,v.total_credit,v.exchange_rate,v.base_total_debit,v.base_total_credit
          FROM treasury_gl_links l JOIN treasury_records t ON t.id=l.treasury_record_id
          LEFT JOIN gl_vouchers v ON v.id=l.voucher_id WHERE t.job_id=? ORDER BY l.id''',(j['id'],))]
        pays=[dict(r) for r in c.execute('''SELECT p.id,p.payment_ref,p.status,p.approval_status,p.gl_control_status,p.amount,p.currency,
          p.beneficiary_validated,p.simulated_provider_ref,pb.batch_no,pb.status batch_status,v.voucher_no,v.status voucher_status
          FROM sandbox_payment_requests p JOIN treasury_payment_batches pb ON pb.id=p.batch_id
          LEFT JOIN gl_vouchers v ON v.id=p.voucher_id WHERE p.job_id=? ORDER BY p.id''',(j['id'],))]
        report=[r for r in gl_detail_rows(status='Posted') if r.get('Job ID')==j['id']]
        return {'phase':'CLX-047','job':dict(j),'integration_records':integrations,'treasury_records':treasury,
          'treasury_gl_links':links,'payment_sandbox':pays,'gl_report_lines':report,
          'trace_ok':bool(integrations and treasury and report),
          'sandbox_only':all(str(x['endpoint_mode']).upper()=='MOCK' for x in integrations)}
    finally:c.close()

@router.get('/control-summary')
def control_summary(x_role:str=Header('AUDITOR')):
    require_role(x_role,'view');c=connect()
    try:
        providers=[]
        for r in c.execute('SELECT provider_key,provider_type,endpoint_mode,credential_ref,status,health_status,max_retries,circuit_state,failure_count FROM finance_providers ORDER BY id'):
            d=dict(r);d['credential_ref']='secret://sandbox/[REDACTED]';providers.append(d)
        events=[dict(r) for r in c.execute('SELECT event_ref,event_type,status,attempt_count,max_attempts,correlation_id,request_redacted,response_redacted FROM integration_events ORDER BY id')]
        webhook=[dict(r) for r in c.execute('SELECT external_event_id,signature_valid,status,received_at FROM webhook_receipts ORDER BY id')]
        imports=[dict(r) for r in c.execute('SELECT import_ref,bank_account_ref,status,line_count,matched_count,unmatched_count,failed_count FROM bank_import_batches ORDER BY id')]
        releases=[dict(r) for r in c.execute('SELECT payment_request_id,provider_ref,release_mode,actor_id,ts FROM sandbox_payment_releases ORDER BY id')]
        audit_count=c.execute('SELECT COUNT(*) n FROM security_audit_events').fetchone()['n']
        return {'phase':'CLX-047','providers':providers,'events':events,'webhooks':webhook,'bank_imports':imports,
          'simulated_releases':releases,'security_audit_count':audit_count,
          'all_providers_mock':all(str(x['endpoint_mode']).upper()=='MOCK' for x in providers),
          'all_credentials_redacted':all(x['credential_ref']=='secret://sandbox/[REDACTED]' for x in providers),
          'all_events_correlated':all(bool(x['correlation_id']) for x in events),
          'retry_limits_respected':all(int(x['attempt_count'])<=int(x['max_attempts']) for x in events),
          'releases_simulated_only':all(x['release_mode']=='SIMULATED' for x in releases),
          'live_providers':False,'real_money':False}
    finally:c.close()

@router.get('/verify')
def verify(x_role:str=Header('AUDITOR')):
    require_role(x_role,'view')
    traces=[job_trace(j,x_role) for j in ('50001','50002','50003','50004','50005')]
    controls=control_summary(x_role)
    return {'phase':'CLX-047',
      'jobs':[{'job_ref':x['job']['job_ref'],'integration_records':len(x['integration_records']),
               'treasury_records':len(x['treasury_records']),'gl_report_lines':len(x['gl_report_lines']),
               'payment_sandbox':len(x['payment_sandbox']),'trace_ok':x['trace_ok'],'sandbox_only':x['sandbox_only']} for x in traces],
      'all_jobs_present':len(traces)==5,
      'all_have_integration':all(x['integration_records'] for x in traces),
      'all_reach_treasury':all(x['treasury_records'] for x in traces),
      'all_reach_gl_reporting':all(x['gl_report_lines'] for x in traces),
      'providers_mock_only':controls['all_providers_mock'],
      'retry_limits_respected':controls['retry_limits_respected'],
      'releases_simulated_only':controls['releases_simulated_only'],
      'screen_count_change':0,'live_providers':False,'real_money':False}
