from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional
import datetime, json, uuid

from .db import connect
from .admin import session, permission_code, roles_for, audit, now
from .clx032_finance_controls import parse_limit_action, limit_subject, sod_violations

router=APIRouter(prefix='/api/clx033',tags=['CLX-033 Finance Transaction Approval Workbench'])

SENSITIVE_ACTIONS={'APPROVE','POST','RELEASE','PAY','WRITE_OFF','REVERSE','CLOSE'}
TX_TYPES={'GL','JOURNAL','AR','AP','TREASURY'}

EXEC_PERM={
 ('JOURNAL','APPROVE'):'JOURNAL_APPROVE',
 ('JOURNAL','POST'):'GL_POST',
 ('JOURNAL','REVERSE'):'GL_POST',
 ('GL','POST'):'GL_POST',
 ('GL','REVERSE'):'GL_POST',
 ('GL','CLOSE'):'PERIOD_CLOSE',
 ('AR','APPROVE'):'AR_APPROVE',
 ('AR','WRITE_OFF'):'AR_WRITE_OFF',
 ('AP','APPROVE'):'AP_APPROVE',
 ('AP','WRITE_OFF'):'AP_WRITE_OFF',
 ('TREASURY','APPROVE'):'TREASURY_APPROVE',
 ('TREASURY','RELEASE'):'TREASURY_RELEASE',
 ('TREASURY','PAY'):'TREASURY_PAY',
 ('TREASURY','REVERSE'):'TREASURY_REVERSE',
}
APPROVER_PERM={
 ('JOURNAL','APPROVE'):'JOURNAL_APPROVE',
 ('JOURNAL','POST'):'JOURNAL_APPROVE',
 ('JOURNAL','REVERSE'):'JOURNAL_APPROVE',
 ('GL','POST'):'JOURNAL_APPROVE',
 ('GL','REVERSE'):'JOURNAL_APPROVE',
 ('GL','CLOSE'):'FINANCE_CONFIG_ADMIN',
 ('AR','APPROVE'):'AR_APPROVE',
 ('AR','WRITE_OFF'):'AR_APPROVE',
 ('AP','APPROVE'):'AP_APPROVE',
 ('AP','WRITE_OFF'):'AP_APPROVE',
 ('TREASURY','APPROVE'):'TREASURY_APPROVE',
 ('TREASURY','RELEASE'):'TREASURY_APPROVE',
 ('TREASURY','PAY'):'TREASURY_APPROVE',
 ('TREASURY','REVERSE'):'TREASURY_APPROVE',
}

class Decision(BaseModel):
    decision:str=Field(pattern='^(APPROVE|REJECT|RETURN_FOR_CORRECTION)$')
    comment:Optional[str]=None
    reason_code:Optional[str]=None

class ReopenBody(BaseModel):
    comment:str=Field(min_length=3,max_length=300)

def _json(v):
    if isinstance(v,dict): return v
    try:return json.loads(v or '{}')
    except:return {}

def _country_for_office(c,office_code):
    if not office_code:return None
    r=c.execute('''SELECT co.country_code FROM iam_offices o JOIN iam_countries co ON co.id=o.country_id
      WHERE o.office_code=?''',(office_code,)).fetchone()
    return r['country_code'] if r else None

def _is_super_admin(c,user_id):
    return any(r['role_code']=='SUPER_ADMIN' for r in roles_for(c,user_id))

def _delegated(c,user_id,perm_code,tx_type,office_code,country_code):
    ts=now()
    for r in c.execute('''SELECT permission_code FROM iam_delegations
      WHERE to_user_id=? AND status='ACTIVE' AND valid_from<=? AND valid_to>=?''',(user_id,ts,ts)):
        raw=r['permission_code']; parts=raw.split(';'); base=parts[0]
        if base!=perm_code: continue
        dims={}
        for item in parts[1:]:
            if '=' in item:
                k,v=item.split('=',1);dims[k]=v
        if dims.get('TX','*') not in ('*',tx_type):continue
        if dims.get('OFFICE','*') not in ('*',office_code or ''):continue
        if dims.get('COUNTRY','*') not in ('*',country_code or ''):continue
        return True
    return False

def _has_entitlement(c,s,perm_code,tx_type,office_code,country_code,enforce_home_office=True):
    if _is_super_admin(c,s['user_id']):return True
    direct=permission_code(c,s['user_id'],perm_code,office_code if enforce_home_office else None)
    return direct or _delegated(c,s['user_id'],perm_code,tx_type,office_code,country_code)

def _limit_resolution(c,s,tx_type,action,amount,currency,office_code,country_code):
    if _is_super_admin(c,s['user_id']):
        return {'decision':'WITHIN_LIMIT','required_level':1,'source':'SUPER_ADMIN'}
    subjects={limit_subject('USER',s['username'])}
    subjects.update(limit_subject('ROLE',r['role_code']) for r in roles_for(c,s['user_id']))
    matches=[]
    ts=now()
    for r in c.execute("SELECT * FROM iam_approval_limits WHERE status='ACTIVE' AND currency=?",(currency.upper(),)):
        if r['role_code'] not in subjects:continue
        d=parse_limit_action(r['action'])
        if d.get('TX')!=tx_type or d.get('ACT')!=action:continue
        if d.get('OFFICE','*') not in ('*',office_code or ''):continue
        if d.get('COUNTRY','*') not in ('*',country_code or ''):continue
        if d.get('FROM','*')!='*' and ts<d['FROM']:continue
        if d.get('TO','*')!='*' and ts>d['TO']:continue
        x=dict(r);x['dimensions']=d;matches.append(x)
    qualifying=[x for x in matches if amount<=float(x['amount_limit'])]
    if qualifying:
        qualifying.sort(key=lambda x:int(x['dimensions'].get('LVL','1')))
        chosen=qualifying[0]
        return {'decision':'WITHIN_LIMIT','required_level':int(chosen['dimensions'].get('LVL','1')),'source':'CLX032','limit':chosen}
    if matches:
        return {'decision':'ESCALATE','reason':'AMOUNT_EXCEEDS_AVAILABLE_LIMIT','matches':matches}
    return {'decision':'WITHIN_LIMIT','required_level':1,'source':'LEGACY_DEFAULT'}

def _chain_key(resource_type,module,rid,action,version):
    return f'{resource_type}:{module}:{rid}:{action}:v{version}'

def _reviews_for_chain(c,chain_id):
    out=[]
    for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE ? ORDER BY id",(f'%"chain_id": "{chain_id}"%',)):
        x=dict(r);x['scope_payload']=_json(x['scope']);out.append(x)
    return out

def _state(reviews):
    if not reviews:return {'status':'NONE','approved_levels':0,'required_levels':0}
    scopes=[x['scope_payload'] for x in reviews]
    required=max(int(x.get('required_levels',1)) for x in scopes)
    approved={int(x['scope_payload'].get('level',1)) for x in reviews if str(x.get('result') or '').startswith('APPROVED')}
    pending=[x for x in reviews if x['status']=='PENDING']
    returned=[x for x in reviews if str(x.get('result') or '').startswith('RETURN_FOR_CORRECTION')]
    rejected=[x for x in reviews if str(x.get('result') or '').startswith('REJECTED')]
    if returned:return {'status':'RETURN_FOR_CORRECTION','approved_levels':len(approved),'required_levels':required}
    if rejected:return {'status':'REJECTED','approved_levels':len(approved),'required_levels':required}
    if len(approved)>=required:return {'status':'APPROVED','approved_levels':len(approved),'required_levels':required}
    return {'status':'PENDING','approved_levels':len(approved),'required_levels':required,'pending':pending}

def _create_level(c,base,level):
    ref='FAPR-'+uuid.uuid4().hex[:10].upper()
    payload={**base,'level':level}
    c.execute('''INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date)
      VALUES(?,?,?,?,?,?)''',(ref,base['requester_user_id'],json.dumps(payload,sort_keys=True),'PENDING',
      f'FINANCE_LEVEL_{level}',(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()))
    return ref

def _ensure_request(c,base):
    chain_id=base['chain_id']; reviews=_reviews_for_chain(c,chain_id)
    if not reviews:
        ref=_create_level(c,base,1)
        audit(c,base['requester_user_ref'],'FINANCE_TRANSACTION_APPROVAL_REQUEST','FINANCE_APPROVAL',chain_id,None,
              {'status':'PENDING','level':1,'required_levels':base['required_levels'],'action':base['action']},
              {'resource_type':base['resource_type'],'module':base['module'],'record_id':base['record_id']})
        return ref,_reviews_for_chain(c,chain_id)
    return None,reviews

def _build_context(c,s,resource_type,module,rid,action,version,amount,currency,tx_type,office_code,country_code,maker_ref,target_url):
    action=action.upper();tx_type=tx_type.upper();currency=(currency or 'USD').upper()
    if action not in SENSITIVE_ACTIONS:raise HTTPException(422,{'code':'NOT_GOVERNED_ACTION'})
    if tx_type not in TX_TYPES:raise HTTPException(422,{'code':'UNKNOWN_FINANCE_TRANSACTION_TYPE'})
    exec_perm=EXEC_PERM.get((tx_type,action))
    if not exec_perm:raise HTTPException(422,{'code':'NO_EXECUTION_ENTITLEMENT_MAPPING','transaction_type':tx_type,'action':action})
    if not _has_entitlement(c,s,exec_perm,tx_type,office_code,country_code):
        raise HTTPException(403,{'code':'FINANCE_ENTITLEMENT_REQUIRED','permission':exec_perm})
    maker_conflict=(tx_type=='JOURNAL' and action=='APPROVE') or (tx_type=='TREASURY' and action in {'RELEASE','PAY'})
    if maker_ref and maker_ref in {s['user_ref'],s['username']} and maker_conflict:
        raise HTTPException(409,{'code':'MAKER_SELF_ACTION_BLOCKED','maker':maker_ref,'actor':s['user_ref']})
    lim=_limit_resolution(c,s,tx_type,action,float(amount or 0),currency,office_code,country_code)
    if lim['decision']=='ESCALATE':
        raise HTTPException(409,{'code':'APPROVAL_LIMIT_EXCEEDED','reason':lim['reason'],'amount':amount,'currency':currency})
    chain_id=_chain_key(resource_type,module,rid,action,version)
    return {
      'kind':'FINANCE_TRANSACTION_APPROVAL','chain_id':chain_id,'resource_type':resource_type,'module':module,
      'record_id':rid,'action':action,'version':version,'transaction_type':tx_type,'amount':float(amount or 0),
      'currency':currency,'office_code':office_code,'country_code':country_code,'maker_ref':maker_ref,
      'requester_user_id':s['user_id'],'requester_user_ref':s['user_ref'],'requester_username':s['username'],
      'required_levels':int(lim['required_level']),'approval_source':lim['source'],'target_url':target_url,
      'execution_permission':exec_perm,'approver_permission':APPROVER_PERM.get((tx_type,action),exec_perm)
    }

def enforce_execution(resource_type,module,rid,action,session_token,version,amount,currency,tx_type,office_code,country_code,maker_ref,target_url):
    if not session_token:return {'legacy_compatibility':True}
    c=connect()
    try:
        s=session(c,session_token)
        chain_id=_chain_key(resource_type,module,rid,action,version)
        existing=_reviews_for_chain(c,chain_id)
        existing_state=_state(existing) if existing else {'status':'NONE'}
        if existing_state['status']=='APPROVED' and action.upper() in {'POST','RELEASE','PAY','REVERSE','CLOSE'}:
            last=[x for x in existing if str(x.get('result') or '').startswith('APPROVED')]
            if last:
                decider=last[-1]['scope_payload'].get('approved_by')
                if decider in {s['user_ref'],s['username']}:
                    raise HTTPException(409,{'code':'EXECUTOR_CANNOT_BE_FINAL_APPROVER','chain_id':chain_id})
        base=_build_context(c,s,resource_type,module,rid,action,version,amount,currency,tx_type,office_code,country_code,maker_ref,target_url)
        _,reviews=_ensure_request(c,base)
        state=_state(reviews)
        if state['status']=='APPROVED':
            return {'approved':True,'chain_id':base['chain_id'],'required_levels':state['required_levels']}
        code={'PENDING':'FINANCE_APPROVAL_PENDING','REJECTED':'FINANCE_APPROVAL_REJECTED',
              'RETURN_FOR_CORRECTION':'FINANCE_RETURNED_FOR_CORRECTION'}.get(state['status'],'FINANCE_APPROVAL_REQUIRED')
        raise HTTPException(409,{'code':code,'chain_id':base['chain_id'],'required_levels':base['required_levels'],
                                 'approved_levels':state.get('approved_levels',0),'target_url':target_url})
    finally:c.close()

def _gl_tx_type(module):
    if module in {'voucher','month-end-journals'}:return 'JOURNAL'
    if module in {'invoice','receipt','credit-note'}:return 'AR'
    if module in {'bills','payment','payment-requisition','debit-note'}:return 'AP'
    return 'GL'

def enforce_gl_action(module,rid,action,session_token,version,reason=None):
    if not session_token:return {'legacy_compatibility':True}
    c=connect()
    try:
        s=session(c,session_token)
        r=c.execute('''SELECT g.*,j.job_ref FROM gl_records g LEFT JOIN jobs j ON j.id=g.job_id
          WHERE g.module=? AND g.id=?''',(module,rid)).fetchone()
        if not r:raise HTTPException(404,'GL record not found')
        p=_json(r['payload_json']); amount=float(p.get('Amount') or p.get('Total Amount') or 0)
        currency=str(p.get('Currency') or 'USD').upper(); maker=None
        v=c.execute('SELECT * FROM gl_vouchers WHERE gl_record_id=?',(rid,)).fetchone()
        if v:
            amount=float(v['total_debit'] or amount);currency=v['currency'] or currency;maker=v['maker_role']
        country=_country_for_office(c,s['office_code'])
    finally:c.close()
    return enforce_execution('GL',module,rid,action,session_token,version,amount,currency,_gl_tx_type(module),
                             s['office_code'],country,maker,f'/api/v1/gl/{module}/{rid}/actions/{action.lower()}')

def _treasury_tx_type(module):
    if 'customer' in module or module in {'receipt-allocation','customer-outstanding'}:return 'AR'
    if 'supplier' in module or 'carrier' in module or module in {'supplier-outstanding'}:return 'AP'
    return 'TREASURY'

def enforce_treasury_action(module,rid,action,session_token,version):
    if not session_token:return {'legacy_compatibility':True}
    c=connect()
    try:
        s=session(c,session_token)
        r=c.execute('SELECT * FROM treasury_records WHERE module=? AND id=?',(module,rid)).fetchone()
        if not r:raise HTTPException(404,'Treasury record not found')
        country=_country_for_office(c,s['office_code'])
        maker=r['maker_id']
        amount=float(r['amount'] or 0);currency=r['currency'] or 'USD'
    finally:c.close()
    return enforce_execution('TREASURY',module,rid,action,session_token,version,amount,currency,_treasury_tx_type(module),
                             s['office_code'],country,maker,f'/api/v1/treasury/{module}/{rid}/actions/{action.lower()}')

@router.get('/workbench')
def workbench(status:Optional[str]=None,q:Optional[str]=None,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        s=session(c,x_m3_session)
        rows=[]
        for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE '%FINANCE_TRANSACTION_APPROVAL%' ORDER BY id DESC"):
            x=dict(r);x['scope_payload']=_json(x['scope']);rows.append(x)
        if status:rows=[x for x in rows if x['status']==status or str(x.get('result') or '').startswith(status)]
        if q:rows=[x for x in rows if q.lower() in json.dumps(x).lower()]
        chains={}
        for x in rows:
            cid=x['scope_payload'].get('chain_id')
            chains.setdefault(cid,[]).append(x)
        items=[]
        for cid,rs in chains.items():
            base=rs[0]['scope_payload'];st=_state(rs)
            items.append({**base,'state':st['status'],'approved_levels':st.get('approved_levels',0),
                          'review_refs':[x['review_ref'] for x in rs],
                          'next_pending':next((x['review_ref'] for x in rs if x['status']=='PENDING'),None)})
        return {'phase':'CLX-033','actor':s['user_ref'],'count':len(items),'items':items}
    finally:c.close()

@router.post('/reviews/{review_ref}/decision')
def decide(review_ref:str,b:Decision,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        s=session(c,x_m3_session)
        r=c.execute('SELECT * FROM iam_access_reviews WHERE review_ref=?',(review_ref,)).fetchone()
        if not r:raise HTTPException(404,'Review not found')
        if r['status']!='PENDING':raise HTTPException(409,{'code':'REVIEW_ALREADY_DECIDED'})
        scope=_json(r['scope'])
        if scope.get('kind')!='FINANCE_TRANSACTION_APPROVAL':raise HTTPException(422,{'code':'WRONG_REVIEW_KIND'})
        if scope.get('maker_ref') in {s['user_ref'],s['username']} or scope.get('requester_user_ref')==s['user_ref']:
            raise HTTPException(409,{'code':'FOUR_EYES_REQUIRED'})
        perm=scope.get('approver_permission')
        if not _has_entitlement(c,s,perm,scope['transaction_type'],scope.get('office_code'),scope.get('country_code'),False):
            raise HTTPException(403,{'code':'APPROVER_ENTITLEMENT_REQUIRED','permission':perm})
        conflicts=sod_violations(c,s['user_id'])
        if conflicts:raise HTTPException(409,{'code':'SOD_CONFLICT','conflicts':[x['conflict_code'] for x in conflicts]})
        lim=_limit_resolution(c,s,scope['transaction_type'],scope['action'],scope['amount'],scope['currency'],
                              scope.get('office_code'),scope.get('country_code'))
        level=int(scope.get('level',1))
        if not _is_super_admin(c,s['user_id']) and (lim['decision']!='WITHIN_LIMIT' or int(lim['required_level'])<level):
            raise HTTPException(409,{'code':'APPROVER_LEVEL_INSUFFICIENT','required_level':level})
        decision=b.decision
        stored={'APPROVE':'APPROVED','REJECT':'REJECTED','RETURN_FOR_CORRECTION':'RETURN_FOR_CORRECTION'}[decision]
        c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",
                  (now(),stored+((':'+b.reason_code) if b.reason_code else ''),r['id']))
        scope['approved_by']=s['user_ref'] if decision=='APPROVE' else None
        c.execute('UPDATE iam_access_reviews SET scope=? WHERE id=?',(json.dumps(scope,sort_keys=True),r['id']))
        audit(c,s['user_ref'],'FINANCE_TRANSACTION_'+stored,'FINANCE_APPROVAL',scope['chain_id'],
              {'status':'PENDING','level':level},{'status':stored,'level':level,'comment':b.comment,'reason_code':b.reason_code},
              {'target_url':scope.get('target_url')})
        if decision=='APPROVE' and level<int(scope['required_levels']):
            next_level=level+1
            existing=_reviews_for_chain(c,scope['chain_id'])
            if not any(int(x['scope_payload'].get('level',0))==next_level for x in existing):
                _create_level(c,scope,next_level)
            return {'status':'NEXT_LEVEL_REQUIRED','chain_id':scope['chain_id'],'next_level':next_level}
        return {'status':decision,'chain_id':scope['chain_id'],'level':level}
    finally:c.close()

@router.post('/chains/{chain_id}/reopen')
def reopen(chain_id:str,b:ReopenBody,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        s=session(c,x_m3_session)
        reviews=_reviews_for_chain(c,chain_id)
        if not reviews:raise HTTPException(404,'Approval chain not found')
        state=_state(reviews)
        if state['status'] not in {'REJECTED','RETURN_FOR_CORRECTION'}:
            raise HTTPException(409,{'code':'CHAIN_NOT_REOPENABLE','state':state['status']})
        base=reviews[0]['scope_payload']; base['version']=int(base['version'])+1
        new_chain=_chain_key(base['resource_type'],base['module'],base['record_id'],base['action'],base['version'])
        base['chain_id']=new_chain;base['requester_user_id']=s['user_id'];base['requester_user_ref']=s['user_ref'];base['requester_username']=s['username']
        ref=_create_level(c,base,1)
        audit(c,s['user_ref'],'FINANCE_TRANSACTION_REOPEN','FINANCE_APPROVAL',new_chain,{'previous_chain':chain_id},
              {'status':'PENDING','review_ref':ref},{'comment':b.comment})
        return {'status':'REOPENED','chain_id':new_chain,'review_ref':ref}
    finally:c.close()

@router.get('/chains/{chain_id}')
def chain(chain_id:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        session(c,x_m3_session);rows=_reviews_for_chain(c,chain_id)
        if not rows:raise HTTPException(404,'Approval chain not found')
        return {'chain_id':chain_id,'state':_state(rows),'reviews':rows}
    finally:c.close()
