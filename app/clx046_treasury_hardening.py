from fastapi import APIRouter,Header,HTTPException
from .db import connect
from .treasury import META,READ_ONLY_MODULES,actor
from .clx045_gl_reporting import gl_detail_rows

router=APIRouter(prefix='/api/clx046',tags=['CLX-046 Treasury AR-AP Hardening'])

GROUP_ORDER=['overview','setup','cash-bank','settlement','payments','cheques','work-queues','reports']

@router.get('/workspace')
def workspace(x_role:str=Header('AUDITOR')):
    actor(x_role,'view')
    groups=[]
    for g in GROUP_ORDER:
        items=[{'key':m['key'],'name':m['name'],'route':m['route'],'read_only':m['key'] in READ_ONLY_MODULES} for m in META if m.get('group')==g]
        if items:groups.append({'group':g,'items':items})
    return {'phase':'CLX-046','groups':groups,'module_count':len(META),'screen_count_change':0,'live_providers':False,'real_money':False}

@router.get('/job/{job_ref}/trace')
def job_trace(job_ref:str,x_role:str=Header('AUDITOR')):
    actor(x_role,'view');c=connect()
    try:
        j=c.execute('''SELECT j.id,j.job_ref,b.booking_ref,c.code customer_code,c.name customer_name,a.code agent_code,a.name agent_name
          FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id
          WHERE j.job_ref=?''',(job_ref,)).fetchone()
        if not j:raise HTTPException(404,'Unknown job')
        treasury=[dict(r) for r in c.execute('SELECT id,module,external_ref,party_name,currency,amount,status,version,source_type,source_ref FROM treasury_records WHERE job_id=? ORDER BY module,id',(j['id'],))]
        links=[dict(r) for r in c.execute('''SELECT l.treasury_record_id,l.link_type,l.source_ref voucher_ref,
          t.module treasury_module,t.external_ref treasury_ref,v.voucher_no,v.voucher_type,v.currency,v.status voucher_status,
          v.total_debit,v.total_credit,v.exchange_rate,v.base_total_debit,v.base_total_credit
          FROM treasury_gl_links l JOIN treasury_records t ON t.id=l.treasury_record_id
          LEFT JOIN gl_vouchers v ON v.id=l.voucher_id WHERE t.job_id=? ORDER BY l.id''',(j['id'],))]
        operations=[dict(r) for r in c.execute("SELECT module,external_ref,status FROM transaction_records WHERE job_id=? AND module IN ('soa','agent-receipt-pay','detention-collection','storage-cost') ORDER BY module",(j['id'],))]
        gl_sources=[dict(r) for r in c.execute("SELECT module,external_ref,status,source_type,source_ref FROM gl_records WHERE job_id=? AND module IN ('invoice','bills','receipt','payment','voucher','wht-deposits') ORDER BY module",(j['id'],))]
        report_lines=[r for r in gl_detail_rows(status='Posted') if r.get('Job ID')==j['id']]
        return {'phase':'CLX-046','job':dict(j),'treasury_records':treasury,'treasury_gl_links':links,'operational_sources':operations,'gl_sources':gl_sources,'gl_report_lines':report_lines,
          'trace_ok':bool(treasury and gl_sources and report_lines)}
    finally:c.close()

@router.get('/control-summary')
def control_summary(x_role:str=Header('AUDITOR')):
    actor(x_role,'view');c=connect()
    try:
        accounts=[dict(r) for r in c.execute('SELECT account_ref,account_type,currency,current_balance,reserved_balance,ROUND(current_balance-reserved_balance,2) net_available,status FROM treasury_accounts ORDER BY account_ref')]
        bank_ex=[dict(r) for r in c.execute("SELECT statement_ref,bank_account_ref,amount,currency,match_status,exception_reason FROM treasury_bank_feed_items WHERE match_status IN ('UNMATCHED','EXCEPTION') ORDER BY id")]
        duplicate_links=[dict(r) for r in c.execute("SELECT treasury_record_id,link_type,COUNT(*) n FROM treasury_gl_links GROUP BY treasury_record_id,link_type HAVING COUNT(*)>1")]
        released_without_gl=[dict(r) for r in c.execute("""SELECT t.id,t.module,t.external_ref FROM treasury_records t
          WHERE t.status='Released' AND t.module IN ('payment-batches','bank-transfer','inter-bank-transfer','customer-refunds','advance-payments')
          AND NOT EXISTS(SELECT 1 FROM treasury_gl_links l WHERE l.treasury_record_id=t.id AND l.link_type='POSTING') ORDER BY t.id""")]
        return {'phase':'CLX-046','cash_position':accounts,'bank_reconciliation_exceptions':bank_ex,'duplicate_gl_links':duplicate_links,
          'released_without_gl':released_without_gl,'read_only_modules':sorted(READ_ONLY_MODULES),'live_providers':False,'real_money':False}
    finally:c.close()

@router.get('/verify')
def verify(x_role:str=Header('AUDITOR')):
    actor(x_role,'view')
    traces=[job_trace(j,x_role) for j in ('50001','50002','50003','50004','50005')]
    return {'phase':'CLX-046','jobs':[{'job_ref':x['job']['job_ref'],'treasury_records':len(x['treasury_records']),'gl_links':len(x['treasury_gl_links']),'gl_report_lines':len(x['gl_report_lines']),'trace_ok':x['trace_ok']} for x in traces],
      'all_jobs_present':len(traces)==5,'all_have_treasury':all(x['treasury_records'] for x in traces),'all_reach_gl_reporting':all(x['gl_report_lines'] for x in traces),
      'screen_count_change':0,'live_providers':False,'real_money':False}
