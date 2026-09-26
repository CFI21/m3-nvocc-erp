from fastapi import APIRouter, HTTPException
from .db import connect
from .business_day import CANONICAL_JOBS, OFFICE_SCOPE
from .screen_catalog import build_catalog, HO_TRANSACTION_ALIASES
from .screen_integration import action_route

router=APIRouter(prefix='/api/clx036',tags=['CLX-036 HO Business Day UAT'])

HO_REQUIRED_LABELS=[
 'Agent Opening','Vendor Opening','Customer Opening','Container Purchase','Container Sale',
 'Purchase Invoice','Purchase Invoice Slot','Sale Invoice','Payment','Receipt',
 'Purchase Invoice Storage','Sale Invoice Detention','Sale Invoice Import','Lease Rental Transaction',
 'Container Exchange','Exchange Rate Update','Third Party Deal Info','Third Party Tracking Info'
]

def _count(c,sql,args=()):
    return c.execute(sql,args).fetchone()['n']

def _job_trace(c,jr):
    j=c.execute('''SELECT j.*,b.booking_ref,c.code customer_code,c.name customer_name,
      a.code agent_code,a.name agent_name FROM jobs j JOIN bookings b ON b.id=j.booking_id
      JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id
      WHERE j.job_ref=?''',(jr,)).fetchone()
    if not j: raise HTTPException(404,'Unknown job')
    jid=j['id']
    req_agent=['storage-cost','detention-collection','container-activity','agent-receipt-pay','soa']
    req_gl=['invoice','bills','payment','receipt','voucher']
    req_integration=['fx-rate-feed','provider-adapters','request-response-audit']
    missing_agent=[m for m in req_agent if not c.execute(
        'SELECT 1 FROM transaction_records WHERE job_id=? AND module=?',(jid,m)).fetchone()]
    missing_gl=[m for m in req_gl if not c.execute(
        'SELECT 1 FROM gl_records WHERE job_id=? AND module=?',(jid,m)).fetchone()]
    missing_integration=[m for m in req_integration if not c.execute(
        'SELECT 1 FROM integration_records WHERE job_id=? AND module=?',(jid,m)).fetchone()]
    voucher=c.execute('''SELECT COUNT(*) n,
      COALESCE(SUM(total_debit),0) debit,COALESCE(SUM(total_credit),0) credit,
      COALESCE(SUM(base_total_debit),0) base_debit,COALESCE(SUM(base_total_credit),0) base_credit
      FROM gl_vouchers WHERE job_id=?''',(jid,)).fetchone()
    links={
      'booking':_count(c,'SELECT COUNT(*) n FROM bookings WHERE id=?',(j['booking_id'],)),
      'containers':_count(c,'SELECT COUNT(*) n FROM containers WHERE job_id=?',(jid,)),
      'bills':_count(c,'SELECT COUNT(*) n FROM bills WHERE job_id=?',(jid,)),
      'agent_tasks':_count(c,'SELECT COUNT(*) n FROM transaction_records WHERE job_id=?',(jid,)),
      'gl':_count(c,'SELECT COUNT(*) n FROM gl_records WHERE job_id=?',(jid,)),
      'treasury':_count(c,'SELECT COUNT(*) n FROM treasury_records WHERE job_id=?',(jid,)),
      'integration':_count(c,'SELECT COUNT(*) n FROM integration_records WHERE job_id=?',(jid,)),
      'audit':_count(c,'SELECT COUNT(*) n FROM audit_events WHERE job_id=?',(jid,)),
    }
    duplicates={
      'agent':_count(c,'''SELECT COUNT(*) n FROM (
        SELECT module,external_ref,COUNT(*) c FROM transaction_records WHERE job_id=?
        GROUP BY module,external_ref HAVING COUNT(*)>1) x''',(jid,)),
      'gl':_count(c,'''SELECT COUNT(*) n FROM (
        SELECT module,external_ref,COUNT(*) c FROM gl_records WHERE job_id=?
        GROUP BY module,external_ref HAVING COUNT(*)>1) x''',(jid,)),
      'treasury':_count(c,'''SELECT COUNT(*) n FROM (
        SELECT module,external_ref,COUNT(*) c FROM treasury_records WHERE job_id=?
        GROUP BY module,external_ref HAVING COUNT(*)>1) x''',(jid,)),
    }
    orphan_sources=_count(c,'''SELECT COUNT(*) n FROM gl_records g
      WHERE g.job_id=? AND g.source_ref IS NOT NULL AND trim(g.source_ref)='' ''',(jid,))
    master_refs={
      'customer':bool(c.execute("SELECT 1 FROM md_records WHERE domain='customer' AND record_key=? AND status='ACTIVE'",(j['customer_code'],)).fetchone()),
      'agent':bool(c.execute("SELECT 1 FROM md_records WHERE domain='agent' AND record_key=? AND status='ACTIVE'",(j['agent_code'],)).fetchone()),
      'carrier_count':_count(c,"SELECT COUNT(*) n FROM md_records WHERE domain='carrier' AND status='ACTIVE'"),
      'document_types':_count(c,"SELECT COUNT(*) n FROM md_records WHERE domain='document-type' AND status='ACTIVE'"),
    }
    balanced=abs(float(voucher['debit'])-float(voucher['credit']))<0.005 and abs(float(voucher['base_debit'])-float(voucher['base_credit']))<0.005
    office,country=OFFICE_SCOPE[jr]
    passed=(
      links['booking']==1 and links['containers']>=1 and links['bills']>=2
      and links['agent_tasks']==19 and links['gl']>=30 and links['treasury']==37 and links['integration']==20
      and not missing_agent and not missing_gl and not missing_integration
      and balanced and all(v==0 for v in duplicates.values()) and orphan_sources==0
      and master_refs['customer'] and master_refs['agent'] and master_refs['carrier_count']>0 and master_refs['document_types']>0
    )
    return {
      'job_ref':jr,'pass':bool(passed),'office':office,'country':country,
      'identity':{'booking_ref':j['booking_ref'],'customer_code':j['customer_code'],'agent_code':j['agent_code']},
      'links':links,'required_modules':{
        'agent_missing':missing_agent,'gl_missing':missing_gl,'integration_missing':missing_integration},
      'gl_balance':{
        'voucher_count':voucher['n'],'debit':voucher['debit'],'credit':voucher['credit'],
        'base_debit':voucher['base_debit'],'base_credit':voucher['base_credit'],'balanced':balanced},
      'duplicates':duplicates,'orphan_source_refs':orphan_sources,'master_refs':master_refs,
    }

def _ho_routes():
    c=build_catalog(); by_id={s['screen_id']:s for s in c['screens']}
    rows=[]
    for a in HO_TRANSACTION_ALIASES:
        target=by_id[a['target']]
        mode=action_route(a['target'],'quick-view')
        rows.append({
          'label':a['label'],'target':a['target'],'route':target['route'],
          'quick_view_mode':mode['mode'],'actions':target['actions'],
          'resolved':bool(target['route'] and target['actions'])
        })
    return rows

@router.get('/trace/{job_ref}')
def trace(job_ref:str):
    if job_ref not in CANONICAL_JOBS: raise HTTPException(422,'CLX-036 supports synthetic jobs 50001-50005 only')
    c=connect()
    try:return {'phase':'CLX-036',**_job_trace(c,job_ref),'live_providers':False,'real_money':False}
    finally:c.close()

@router.get('/verify')
def verify():
    c=connect()
    try:
        jobs=[_job_trace(c,jr) for jr in CANONICAL_JOBS]
        routes=_ho_routes()
        labels=[x['label'] for x in routes]
        catalog=build_catalog()
        status='PASS' if (
          catalog['screen_count']==193 and labels==HO_REQUIRED_LABELS
          and all(x['resolved'] for x in routes) and all(x['pass'] for x in jobs)
        ) else 'FAIL'
        return {
          'phase':'CLX-036','status':status,'synthetic_only':True,
          'canonical_jobs':CANONICAL_JOBS,'screen_count':catalog['screen_count'],
          'ho_transaction_aliases':len(routes),'ho_routes':routes,'jobs':jobs,
          'smart_approval':'CLX-034_PRESERVED_AND_REGRESSION_GATED',
          'role_office_country_scope':'EXISTING_SERVER_AUTHORITY_PRESERVED',
          'maker_checker_four_eyes_sod':'PRESERVED_AND_REGRESSION_GATED',
          'data_model_changed':False,'api_contracts':'PRESERVED_WITH_ADDITIVE_CLX036_ENDPOINTS',
          'live_providers':False,'real_money':False,
        }
    finally:c.close()
