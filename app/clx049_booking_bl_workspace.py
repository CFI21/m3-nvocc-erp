from fastapi import APIRouter,Header,HTTPException
import json
from .db import connect

router=APIRouter(prefix='/api/clx049',tags=['CLX-049 Booking BL Same Page Workspace'])

BL_TABS=['Booking Info','Release Instruction','Delivery Order','Lock Info','Authorization']
BOOKING_TABS=['Booking Info','Equipment','Other Info']
FLOW_KEYS={'special-rates-request','booking','bl','switch-bl','delivery-order','vessel-lock'}

def _payload(r):
    d=dict(r) if r else None
    if not d:return None
    raw=d.pop('payload_json',None)
    d['fields']=json.loads(raw or '{}')
    return d

def _tx(c,jid,module):
    r=c.execute('''SELECT t.*,j.job_ref,b.booking_ref,c.name customer_name,a.code agent_code,
      j.pol,j.pod,bl.bill_no hbl_no
      FROM transaction_records t
      JOIN jobs j ON j.id=t.job_id
      JOIN bookings b ON b.id=t.booking_id
      JOIN customers c ON c.id=t.customer_id
      JOIN agents a ON a.id=t.agent_id
      LEFT JOIN bills bl ON bl.id=t.bill_id
      WHERE t.job_id=? AND t.module=? ORDER BY t.id LIMIT 1''',(jid,module)).fetchone()
    return _payload(r)

def _common_party_match(c,name):
    if not name:return None
    r=c.execute("""SELECT record_key,display_name,status,payload_json FROM md_records
      WHERE domain='common-party' AND lower(trim(display_name))=lower(trim(?)) AND status='ACTIVE'
      ORDER BY record_key LIMIT 1""",(name,)).fetchone()
    if not r:return {'status':'UNRESOLVED','display_name':name,'record_key':None}
    d=dict(r);d.pop('payload_json',None);d['status']='LINKED';return d

def _booking_party_integrity(c,jid):
    r=c.execute("""SELECT j.job_ref,j.customer_id job_customer_id,j.agent_id job_agent_id,
      b.customer_id booking_customer_id,b.agent_id booking_agent_id,
      c.code customer_code,c.name customer_name,a.code agent_code,a.name agent_name
      FROM jobs j JOIN bookings b ON b.id=j.booking_id
      JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id
      WHERE j.id=?""",(jid,)).fetchone()
    if not r:return None
    d=dict(r)
    d['customer_booking_match']=d['job_customer_id']==d['booking_customer_id']
    d['agent_booking_match']=d['job_agent_id']==d['booking_agent_id']
    d['relationship_scope']='BOOKING_JOB_ONLY'
    d['customer_common_party']=_common_party_match(c,d['customer_name'])
    d['agent_common_party']=_common_party_match(c,d['agent_name'])
    return d

def _audit(c,jid,module=None):
    q='SELECT id,event_id,ts,actor_role,actor_scope,action,module,transaction_id,before_json,after_json,metadata_json FROM audit_events WHERE job_id=?'
    args=[jid]
    if module:q+=' AND module=?';args.append(module)
    q+=' ORDER BY id DESC LIMIT 50'
    return [dict(r) for r in c.execute(q,args)]

@router.get('/workspace/{job_ref}')
def workspace(job_ref:str,x_role:str=Header('VIEWER'),x_agent_scope:str|None=Header(None)):
    c=connect()
    try:
        j=c.execute('''SELECT j.id,j.job_ref,j.pol,j.pod,j.operational_status,
          b.booking_ref,c.code customer_code,c.name customer_name,a.code agent_code,a.name agent_name,
          v.voyage_no,vs.name vessel_name,bl.bill_no hbl_no
          FROM jobs j
          JOIN bookings b ON b.id=j.booking_id
          JOIN customers c ON c.id=j.customer_id
          JOIN agents a ON a.id=j.agent_id
          JOIN voyages v ON v.id=j.voyage_id
          JOIN vessels vs ON vs.id=v.vessel_id
          LEFT JOIN bills bl ON bl.job_id=j.id AND bl.kind='HBL'
          WHERE j.job_ref=?''',(job_ref,)).fetchone()
        if not j:raise HTTPException(404,'Unknown job')
        if x_role.upper()=='AGENT' and (not x_agent_scope or x_agent_scope!=j['agent_code']):
            raise HTTPException(404,'Job outside agent scope')
        jid=j['id']
        srr=_tx(c,jid,'special-rates-request')
        booking=_tx(c,jid,'booking')
        bl=_tx(c,jid,'bl')
        do=_tx(c,jid,'delivery-order')
        lock=_tx(c,jid,'vessel-lock')
        switch=_tx(c,jid,'switch-bl')
        workflow=c.execute('SELECT * FROM workflow_states WHERE job_id=?',(jid,)).fetchone()
        holds=[dict(r) for r in c.execute('SELECT code,active,created_at,cleared_at FROM workflow_holds WHERE job_id=? ORDER BY id',(jid,))]
        sw_hist=[dict(r) for r in c.execute('SELECT * FROM switch_bl_history WHERE job_id=? ORDER BY id DESC',(jid,))]
        return {
          'phase':'CLX-049',
          'context':dict(j),
          'party_context':_booking_party_integrity(c,jid),
          'party_relationship_model':'INDEPENDENT_COMMON_PARTIES_LINKED_BY_BOOKING_JOB_ONLY',
          'special_rate_request':srr,
          'booking':booking,
          'bl':bl,
          'delivery_order':do,
          'lock_info':{'vessel_lock':lock,'workflow':dict(workflow) if workflow else None,'holds':holds},
          'authorization':{
            'bl_status':bl['status'] if bl else None,
            'draft_approval':(bl or {}).get('fields',{}).get('Draft Approval') if bl else None,
            'audit':_audit(c,jid,'bl')
          },
          'switch_bl':switch,
          'switch_bl_history':sw_hist,
          'booking_tabs':BOOKING_TABS,
          'bl_tabs':BL_TABS,
          'deep_links':{
            'special_rate_request':'agent-tasks::special-rates-request',
            'booking':'agent-tasks::booking',
            'bl':'agent-tasks::bl',
            'delivery_order':'agent-tasks::delivery-order',
            'lock_info':'agent-tasks::vessel-lock',
            'switch_bl':'agent-tasks::switch-bl'
          },
          'authoritative_sources':{
            'booking':'transaction_records:booking + bookings',
            'bl':'transaction_records:bl + bills',
            'delivery_order':'transaction_records:delivery-order',
            'lock_info':'transaction_records:vessel-lock + workflow_states + workflow_holds',
            'authorization':'transaction_records:bl + audit_events',
            'switch_bl':'transaction_records:switch-bl + switch_bl_history'
          },
          'access_model':'AGENT_USER_SCOPED_TO_AGENT; CUSTOMER VISIBILITY DERIVED FROM AUTHORIZED BOOKING/JOB',
          'permanent_customer_agent_master_link':False,
          'parallel_records_created':False,
          'live_providers':False,
          'real_money':False
        }
    finally:c.close()

@router.get('/trace/{job_ref}')
def trace(job_ref:str,x_role:str=Header('AUDITOR'),x_agent_scope:str|None=Header(None)):
    w=workspace(job_ref,x_role,x_agent_scope)
    refs={
      'special_rate_request':w['special_rate_request']['external_ref'] if w['special_rate_request'] else None,
      'booking':w['booking']['external_ref'] if w['booking'] else None,
      'booking_master':w['context']['booking_ref'],
      'bl':w['bl']['external_ref'] if w['bl'] else None,
      'hbl':w['context']['hbl_no'],
      'delivery_order':w['delivery_order']['external_ref'] if w['delivery_order'] else None,
      'vessel_lock':w['lock_info']['vessel_lock']['external_ref'] if w['lock_info']['vessel_lock'] else None,
      'switch_bl':w['switch_bl']['external_ref'] if w['switch_bl'] else None,
    }
    srr_fields=(w['special_rate_request'] or {}).get('fields',{})
    booking_fields=(w['booking'] or {}).get('fields',{})
    bl_fields=(w['bl'] or {}).get('fields',{})
    do_fields=(w['delivery_order'] or {}).get('fields',{})
    switch_fields=(w['switch_bl'] or {}).get('fields',{})
    party=w.get('party_context') or {}
    checks={
      'booking_customer_matches_job':bool(party.get('customer_booking_match')),
      'booking_agent_matches_job':bool(party.get('agent_booking_match')),
      'relationship_is_booking_job_only':party.get('relationship_scope')=='BOOKING_JOB_ONLY',
      'srr_booking_link':not w['special_rate_request'] or not srr_fields.get('Booking Ref') or srr_fields.get('Booking Ref') in {w['context']['booking_ref'],booking_fields.get('Booking No.')},
      'booking_job_link':bool(w['booking'] and w['booking']['job_ref']==job_ref),
      'bl_job_link':bool(w['bl'] and w['bl']['job_ref']==job_ref),
      'delivery_order_same_job':bool(w['delivery_order'] and w['delivery_order']['job_ref']==job_ref),
      'lock_same_job':bool(w['lock_info']['vessel_lock'] and w['lock_info']['vessel_lock']['job_ref']==job_ref),
      'switch_original_bl_link':bool(w['switch_bl'] and switch_fields.get('Original B/L') and switch_fields.get('Original B/L') in {w['context']['hbl_no'],bl_fields.get('B/L No.'),bl_fields.get('HBL')}),
      'delivery_order_bl_link':bool(w['delivery_order'] and do_fields.get('HBL') and do_fields.get('HBL') in {w['context']['hbl_no'],bl_fields.get('B/L No.'),bl_fields.get('HBL')}),
      'same_authoritative_context':all(x is None or x.get('job_ref')==job_ref for x in [w['special_rate_request'],w['booking'],w['bl'],w['delivery_order'],w['lock_info']['vessel_lock'],w['switch_bl']])
    }
    return {'phase':'CLX-049','job_ref':job_ref,'refs':refs,'checks':checks,'pass':all(checks.values()),'screen_count_change':0,'parallel_records_created':False}

@router.get('/booking-party-link/verify')
def booking_party_link_verify():
    c=connect()
    try:
        pairs=[dict(r) for r in c.execute("""SELECT j.job_ref,b.booking_ref,c.code customer_code,a.code agent_code,
          CASE WHEN j.customer_id=b.customer_id THEN 1 ELSE 0 END customer_match,
          CASE WHEN j.agent_id=b.agent_id THEN 1 ELSE 0 END agent_match
          FROM jobs j JOIN bookings b ON b.id=j.booking_id
          JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id
          ORDER BY j.job_ref""")]
        agent_customer_grants=[dict(r) for r in c.execute("""SELECT u.username,p.party_type,p.party_key
          FROM iam_users u
          JOIN iam_user_roles ur ON ur.user_id=u.id AND ur.status='ACTIVE'
          JOIN iam_roles r ON r.id=ur.role_id AND r.role_code='AGENT'
          JOIN iam_party_access p ON p.user_id=u.id AND p.status='ACTIVE'
          WHERE p.party_type='CUSTOMER'""")]
        return {
          'relationship_model':'BOOKING_JOB_ONLY',
          'customer_master_independent':True,
          'agent_master_independent':True,
          'permanent_customer_agent_master_link':False,
          'booking_job_pairs':pairs,
          'booking_job_pair_mismatches':sum(1 for x in pairs if not x['customer_match'] or not x['agent_match']),
          'agent_users_with_permanent_customer_party_grants':agent_customer_grants,
          'cross_agent_customer_wide_access':'DENIED_BY_DESIGN',
          'many_to_many_supported':'BY_MULTIPLE_INDEPENDENT_BOOKINGS',
          'common_party_matching':'CUSTOMER_AND_AGENT_RESOLVED_SEPARATELY; NO DIRECT CUSTOMER_TO_AGENT_MATCH',
          'pass':all(x['customer_match'] and x['agent_match'] for x in pairs) and not agent_customer_grants
        }
    finally:c.close()

@router.get('/verify')
def verify(x_role:str=Header('AUDITOR')):
    rows=[trace(j,x_role,None) for j in ('50001','50002','50003','50004','50005')]
    return {
      'phase':'CLX-049',
      'jobs':rows,
      'all_jobs_pass':all(r['pass'] for r in rows),
      'bl_tab_order':BL_TABS,
      'booking_tab_order':BOOKING_TABS,
      'screen_count_change':0,
      'data_model_change':False,
      'parallel_records_created':False,
      'live_providers':False,
      'real_money':False
    }
