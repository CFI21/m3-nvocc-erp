from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field
from typing import Optional
import datetime
import json
import uuid

from .db import connect
from .admin import session, permission, permission_code, roles_for, audit, now

router=APIRouter(prefix='/api/clx031', tags=['CLX-031 Finance Approval + Delegation + SoD'])

TX_TYPES={'GL','JOURNAL','AR','AP','TREASURY'}
APPROVAL_ACTIONS={'APPROVE','POST','RELEASE','PAY','WRITE_OFF','REVERSE','CLOSE'}
FINANCE_PERMISSION_CODES={
    'GL_VIEW','GL_CREATE','GL_EDIT','GL_DISABLE','GL_POST','JOURNAL_CREATE','JOURNAL_APPROVE',
    'ACCOUNT_MAPPING_MANAGE','COST_PROFIT_CENTER_MANAGE','TAX_CURRENCY_MANAGE','PERIOD_CLOSE','FINANCE_CONFIG_ADMIN'
}
DEFAULT_SOD=[
    ('SOD-JOURNAL-MAKER-CHECKER','PERM:JOURNAL_CREATE','PERM:JOURNAL_APPROVE','Journal maker cannot approve own approval capability','CRITICAL'),
    ('SOD-POSTER-APPROVER','PERM:GL_POST','PERM:JOURNAL_APPROVE','Posting and approval capabilities must remain segregated','CRITICAL'),
    ('SOD-PAYMENT-MAKER-RELEASER','CAP:PAYMENT_MAKER','CAP:PAYMENT_RELEASER','Payment maker cannot release the same payment','CRITICAL'),
]


class ApprovalLimitRequest(BaseModel):
    subject_type:str=Field(pattern='^(ROLE|USER)$')
    subject_code:str
    currency:str=Field(min_length=3,max_length=3)
    amount_limit:float=Field(gt=0)
    transaction_type:str
    action:str
    level:int=Field(ge=1,le=9)
    office_code:Optional[str]=None
    country_code:Optional[str]=None
    valid_from:Optional[str]=None
    valid_to:Optional[str]=None
    reason:str=Field(min_length=3,max_length=300)


class DelegationRequest(BaseModel):
    from_username:str
    to_username:str
    permission_code:str
    transaction_type:Optional[str]=None
    office_code:Optional[str]=None
    country_code:Optional[str]=None
    valid_from:str
    valid_to:str
    reason:str=Field(min_length=3,max_length=300)


class SodRequest(BaseModel):
    conflict_code:str
    capability_a:str
    capability_b:str
    reason:str=Field(min_length=3,max_length=300)
    severity:str=Field(pattern='^(LOW|MEDIUM|HIGH|CRITICAL)$')


class Decision(BaseModel):
    decision:str=Field(pattern='^(APPROVE|REJECT)$')
    comment:Optional[str]=None


class EvaluateRequest(BaseModel):
    username:str
    currency:str=Field(min_length=3,max_length=3)
    amount:float=Field(ge=0)
    transaction_type:str
    action:str
    office_code:Optional[str]=None
    country_code:Optional[str]=None


def require_admin(c,s):
    if not permission(c,s['user_id'],'identity','admin',s['office_code']):
        raise HTTPException(403,{'code':'PERMISSION_DENIED'})


def auth_admin(c,token):
    s=session(c,token)
    require_admin(c,s)
    return s


def validate_dates(vf,vt):
    if vf and vt and vt<=vf:
        raise HTTPException(422,{'code':'INVALID_EFFECTIVE_DATES'})


def validate_tx_action(tx,action):
    tx=tx.upper(); action=action.upper()
    if tx not in TX_TYPES: raise HTTPException(422,{'code':'INVALID_TRANSACTION_TYPE','allowed':sorted(TX_TYPES)})
    if action not in APPROVAL_ACTIONS: raise HTTPException(422,{'code':'INVALID_APPROVAL_ACTION','allowed':sorted(APPROVAL_ACTIONS)})
    return tx,action


def limit_subject(subject_type,subject_code):
    return f'{subject_type.upper()}:{subject_code.upper()}'


def encode_limit_action(b):
    tx,act=validate_tx_action(b.transaction_type,b.action)
    validate_dates(b.valid_from,b.valid_to)
    parts={
        'TX':tx,'ACT':act,'LVL':str(b.level),
        'OFFICE':(b.office_code or '*').upper(),
        'COUNTRY':(b.country_code or '*').upper(),
        'FROM':b.valid_from or '*','TO':b.valid_to or '*'
    }
    return ';'.join(f'{k}={v}' for k,v in parts.items())


def parse_limit_action(value):
    if '=' not in value:
        return {'legacy_action':value,'TX':'LEGACY','ACT':value,'LVL':'1','OFFICE':'*','COUNTRY':'*','FROM':'*','TO':'*'}
    out={}
    for item in value.split(';'):
        if '=' in item:
            k,v=item.split('=',1); out[k]=v
    return out


def encode_delegated_permission(b):
    tx=(b.transaction_type or '*').upper()
    if tx!='*' and tx not in TX_TYPES: raise HTTPException(422,{'code':'INVALID_TRANSACTION_TYPE'})
    return f"{b.permission_code};TX={tx};OFFICE={(b.office_code or '*').upper()};COUNTRY={(b.country_code or '*').upper()}"


def base_permission(encoded):
    return encoded.split(';',1)[0]


def user_capabilities(c,user_id):
    caps=set()
    for r in roles_for(c,user_id):
        caps.add('ROLE:'+r['role_code'])
        for p in c.execute('''SELECT p.permission_code FROM iam_role_permissions rp
          JOIN iam_permissions p ON p.id=rp.permission_id
          WHERE rp.role_id=(SELECT id FROM iam_roles WHERE role_code=?) AND rp.effect='ALLOW' ''',(r['role_code'],)):
            caps.add('PERM:'+p['permission_code'])
    ts=now()
    for p in c.execute('''SELECT permission_code FROM iam_temporary_access
      WHERE user_id=? AND status='ACTIVE' AND valid_from<=? AND valid_to>=?''',(user_id,ts,ts)):
        caps.add('PERM:'+p['permission_code'])
    for d in c.execute('''SELECT permission_code FROM iam_delegations
      WHERE to_user_id=? AND status='ACTIVE' AND valid_from<=? AND valid_to>=?''',(user_id,ts,ts)):
        caps.add('PERM:'+base_permission(d['permission_code']))
    return caps


def active_sod_conflicts(c):
    return [dict(r) for r in c.execute("SELECT * FROM iam_sod_conflicts WHERE status='ACTIVE' ORDER BY severity DESC,conflict_code")]


def sod_violations(c,user_id,proposed=None):
    caps=user_capabilities(c,user_id)
    if proposed:
        caps.add(proposed if ':' in proposed else 'PERM:'+proposed)
    hits=[]
    for x in active_sod_conflicts(c):
        if x['role_a'] in caps and x['role_b'] in caps:
            hits.append(x)
        elif x['role_b'] in caps and x['role_a'] in caps:
            hits.append(x)
    return hits


def create_review(c,user_id,kind,payload,maker):
    ref='REV-'+uuid.uuid4().hex[:10].upper()
    scope=json.dumps({'kind':kind,'request':payload,'maker':maker},sort_keys=True)
    due=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=2)).date().isoformat()
    c.execute('''INSERT INTO iam_access_reviews(review_ref,user_id,scope,status,reviewer,due_date)
      VALUES(?,?,?,?,?,?)''',(ref,user_id,scope,'PENDING','INDEPENDENT_CHECKER',due))
    return ref


def lookup_subject(c,b):
    if b.subject_type=='USER':
        u=c.execute('''SELECT u.*,o.office_code,co.country_code FROM iam_users u
          JOIN iam_offices o ON o.id=u.home_office_id JOIN iam_countries co ON co.id=o.country_id
          WHERE upper(u.username)=upper(?)''',(b.subject_code,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        return u
    r=c.execute('SELECT * FROM iam_roles WHERE upper(role_code)=upper(?)',(b.subject_code,)).fetchone()
    if not r: raise HTTPException(404,'Role not found')
    return r


def apply_limit(c,payload,actor):
    b=ApprovalLimitRequest(**payload)
    subj=lookup_subject(c,b)
    key=limit_subject(b.subject_type,b.subject_code)
    action_key=encode_limit_action(b)
    currency=b.currency.upper()
    existing=c.execute('''SELECT * FROM iam_approval_limits
      WHERE role_code=? AND currency=? AND action=?''',(key,currency,action_key)).fetchone()
    before=dict(existing) if existing else None
    if existing:
        c.execute("UPDATE iam_approval_limits SET amount_limit=?,status='ACTIVE' WHERE id=?",(b.amount_limit,existing['id']))
        rid=existing['id']
    else:
        cur=c.execute('''INSERT INTO iam_approval_limits(role_code,currency,amount_limit,action,status)
          VALUES(?,?,?,?, 'ACTIVE')''',(key,currency,b.amount_limit,action_key))
        rid=cur.lastrowid
    after={'id':rid,'subject':key,'currency':currency,'amount_limit':b.amount_limit,'action':action_key,'status':'ACTIVE'}
    audit(c,actor,'FINANCE_APPROVAL_LIMIT_SET','APPROVAL_LIMIT',str(rid),before,after,{'reason':b.reason})
    return after


def apply_delegation(c,payload,actor):
    b=DelegationRequest(**payload)
    validate_dates(b.valid_from,b.valid_to)
    if b.from_username.lower()==b.to_username.lower():
        raise HTTPException(422,{'code':'SELF_DELEGATION_NOT_ALLOWED'})
    src=c.execute('SELECT * FROM iam_users WHERE username=?',(b.from_username,)).fetchone()
    dst=c.execute('SELECT * FROM iam_users WHERE username=?',(b.to_username,)).fetchone()
    if not src or not dst: raise HTTPException(404,'Delegator or delegate not found')
    if b.permission_code not in FINANCE_PERMISSION_CODES:
        raise HTTPException(422,{'code':'NOT_FINANCE_PERMISSION'})
    if not permission_code(c,src['id'],b.permission_code):
        raise HTTPException(403,{'code':'DELEGATOR_LACKS_PERMISSION','permission':b.permission_code})
    conflicts=sod_violations(c,dst['id'],'PERM:'+b.permission_code)
    if conflicts:
        raise HTTPException(409,{'code':'SOD_CONFLICT','conflicts':[x['conflict_code'] for x in conflicts]})
    encoded=encode_delegated_permission(b)
    ref='DEL-'+uuid.uuid4().hex[:10].upper()
    c.execute('''INSERT INTO iam_delegations(delegation_ref,from_user_id,to_user_id,permission_code,valid_from,valid_to,status,approved_by)
      VALUES(?,?,?,?,?,?,?,?)''',(ref,src['id'],dst['id'],encoded,b.valid_from,b.valid_to,'ACTIVE',actor))
    after={'delegation_ref':ref,'from':b.from_username,'to':b.to_username,'permission':encoded,'valid_from':b.valid_from,'valid_to':b.valid_to,'status':'ACTIVE'}
    audit(c,actor,'FINANCE_DELEGATION_ACTIVATE','DELEGATION',ref,None,after,{'reason':b.reason})
    return after


def apply_sod(c,payload,actor):
    b=SodRequest(**payload)
    a=b.capability_a if ':' in b.capability_a else 'PERM:'+b.capability_a
    bb=b.capability_b if ':' in b.capability_b else 'PERM:'+b.capability_b
    if a==bb: raise HTTPException(422,{'code':'SOD_CAPABILITIES_MUST_DIFFER'})
    existing=c.execute('SELECT * FROM iam_sod_conflicts WHERE conflict_code=?',(b.conflict_code,)).fetchone()
    before=dict(existing) if existing else None
    if existing:
        c.execute('''UPDATE iam_sod_conflicts SET role_a=?,role_b=?,reason=?,severity=?,status='ACTIVE' WHERE id=?''',
          (a,bb,b.reason,b.severity,existing['id']))
        rid=existing['id']
    else:
        cur=c.execute('''INSERT INTO iam_sod_conflicts(conflict_code,role_a,role_b,reason,severity,status)
          VALUES(?,?,?,?,?,'ACTIVE')''',(b.conflict_code,a,bb,b.reason,b.severity))
        rid=cur.lastrowid
    after={'id':rid,'conflict_code':b.conflict_code,'capability_a':a,'capability_b':bb,'reason':b.reason,'severity':b.severity,'status':'ACTIVE'}
    audit(c,actor,'FINANCE_SOD_SET','SOD_CONFLICT',b.conflict_code,before,after)
    return after


@router.get('/workspace')
def workspace(
    q:Optional[str]=None,
    status:Optional[str]=None,
    x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')
):
    c=connect()
    try:
        auth_admin(c,x_m3_session)
        limits=[]
        for r in c.execute('SELECT * FROM iam_approval_limits ORDER BY role_code,currency,amount_limit'):
            x=dict(r); x['dimensions']=parse_limit_action(x['action']); limits.append(x)
        delegations=[dict(r) for r in c.execute('''SELECT d.*,fu.username from_username,tu.username to_username
          FROM iam_delegations d JOIN iam_users fu ON fu.id=d.from_user_id JOIN iam_users tu ON tu.id=d.to_user_id ORDER BY d.id DESC''')]
        sod=active_sod_conflicts(c)
        reviews=[dict(r) for r in c.execute("SELECT * FROM iam_access_reviews WHERE scope LIKE '%FINANCE_%' ORDER BY id DESC")]
        audit_rows=[dict(r) for r in c.execute("SELECT * FROM iam_audit_events WHERE action LIKE 'FINANCE_%' ORDER BY id DESC LIMIT 100")]
    finally:
        c.close()
    if q:
        n=q.lower()
        limits=[x for x in limits if n in json.dumps(x).lower()]
        delegations=[x for x in delegations if n in json.dumps(x).lower()]
        sod=[x for x in sod if n in json.dumps(x).lower()]
        reviews=[x for x in reviews if n in json.dumps(x).lower()]
        audit_rows=[x for x in audit_rows if n in json.dumps(x).lower()]
    if status:
        limits=[x for x in limits if x['status']==status]
        delegations=[x for x in delegations if x['status']==status]
        reviews=[x for x in reviews if x['status']==status]
    return {'phase':'CLX-031','approval_limits':limits,'delegations':delegations,'sod_conflicts':sod,'access_reviews':reviews,'audit':audit_rows}


@router.post('/approval-limits')
def request_limit(b:ApprovalLimitRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        s=auth_admin(c,x_m3_session)
        lookup_subject(c,b); encode_limit_action(b)
        ref=create_review(c,s['user_id'],'FINANCE_APPROVAL_LIMIT',b.model_dump(),s['user_ref'])
        audit(c,s['user_ref'],'FINANCE_APPROVAL_LIMIT_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':b.model_dump()})
        return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    finally:c.close()


@router.post('/delegations')
def request_delegation(b:DelegationRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        s=auth_admin(c,x_m3_session)
        validate_dates(b.valid_from,b.valid_to)
        if b.from_username.lower()==b.to_username.lower(): raise HTTPException(422,{'code':'SELF_DELEGATION_NOT_ALLOWED'})
        src=c.execute('SELECT * FROM iam_users WHERE username=?',(b.from_username,)).fetchone()
        dst=c.execute('SELECT * FROM iam_users WHERE username=?',(b.to_username,)).fetchone()
        if not src or not dst: raise HTTPException(404,'Delegator or delegate not found')
        if b.permission_code not in FINANCE_PERMISSION_CODES: raise HTTPException(422,{'code':'NOT_FINANCE_PERMISSION'})
        if not permission_code(c,src['id'],b.permission_code): raise HTTPException(403,{'code':'DELEGATOR_LACKS_PERMISSION'})
        hits=sod_violations(c,dst['id'],'PERM:'+b.permission_code)
        if hits: raise HTTPException(409,{'code':'SOD_CONFLICT','conflicts':[x['conflict_code'] for x in hits]})
        ref=create_review(c,dst['id'],'FINANCE_DELEGATION',b.model_dump(),s['user_ref'])
        audit(c,s['user_ref'],'FINANCE_DELEGATION_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':b.model_dump()})
        return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    finally:c.close()


@router.post('/sod-conflicts')
def request_sod(b:SodRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        s=auth_admin(c,x_m3_session)
        ref=create_review(c,s['user_id'],'FINANCE_SOD',b.model_dump(),s['user_ref'])
        audit(c,s['user_ref'],'FINANCE_SOD_REQUEST','ACCESS_REVIEW',ref,None,{'status':'PENDING','request':b.model_dump()})
        return {'status':'PENDING_APPROVAL','review_ref':ref,'four_eyes':True}
    finally:c.close()


@router.post('/reviews/{review_ref}/decision')
def decide(review_ref:str,b:Decision,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        s=auth_admin(c,x_m3_session)
        r=c.execute('SELECT * FROM iam_access_reviews WHERE review_ref=?',(review_ref,)).fetchone()
        if not r: raise HTTPException(404,'Review not found')
        if r['status']!='PENDING': raise HTTPException(409,{'code':'REVIEW_ALREADY_DECIDED'})
        scope=json.loads(r['scope'])
        if scope.get('maker')==s['user_ref']: raise HTTPException(409,{'code':'FOUR_EYES_REQUIRED'})
        if b.decision=='REJECT':
            c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",
                      (now(),'REJECTED'+((':'+b.comment) if b.comment else ''),r['id']))
            audit(c,s['user_ref'],'FINANCE_CONTROL_REJECT','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'REJECTED'})
            return {'status':'REJECTED','review_ref':review_ref}
        kind=scope.get('kind')
        if kind=='FINANCE_APPROVAL_LIMIT':
            out=apply_limit(c,scope['request'],s['user_ref']); key='approval_limit'
        elif kind=='FINANCE_DELEGATION':
            out=apply_delegation(c,scope['request'],s['user_ref']); key='delegation'
        elif kind=='FINANCE_SOD':
            out=apply_sod(c,scope['request'],s['user_ref']); key='sod_conflict'
        else:
            raise HTTPException(422,{'code':'UNSUPPORTED_REVIEW_KIND'})
        c.execute("UPDATE iam_access_reviews SET status='COMPLETED',completed_at=?,result=? WHERE id=?",
                  (now(),'APPROVED'+((':'+b.comment) if b.comment else ''),r['id']))
        audit(c,s['user_ref'],'FINANCE_CONTROL_APPROVE','ACCESS_REVIEW',review_ref,{'status':'PENDING'},{'status':'APPROVED',key:out})
        return {'status':'APPROVED','review_ref':review_ref,key:out}
    finally:c.close()


@router.post('/approval-limits/{limit_id}/revoke')
def revoke_limit(limit_id:int,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        s=auth_admin(c,x_m3_session)
        r=c.execute('SELECT * FROM iam_approval_limits WHERE id=?',(limit_id,)).fetchone()
        if not r: raise HTTPException(404,'Limit not found')
        before=dict(r); c.execute("UPDATE iam_approval_limits SET status='INACTIVE' WHERE id=?",(limit_id,))
        audit(c,s['user_ref'],'FINANCE_APPROVAL_LIMIT_REVOKE','APPROVAL_LIMIT',str(limit_id),before,{'status':'INACTIVE'})
        return {'status':'REVOKED','id':limit_id}
    finally:c.close()


@router.post('/delegations/{delegation_ref}/revoke')
def revoke_delegation(delegation_ref:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        s=auth_admin(c,x_m3_session)
        r=c.execute('SELECT * FROM iam_delegations WHERE delegation_ref=?',(delegation_ref,)).fetchone()
        if not r: raise HTTPException(404,'Delegation not found')
        before=dict(r); c.execute("UPDATE iam_delegations SET status='REVOKED' WHERE id=?",(r['id'],))
        audit(c,s['user_ref'],'FINANCE_DELEGATION_REVOKE','DELEGATION',delegation_ref,before,{'status':'REVOKED'})
        return {'status':'REVOKED','delegation_ref':delegation_ref}
    finally:c.close()


@router.post('/sod-conflicts/{conflict_code}/revoke')
def revoke_sod(conflict_code:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        s=auth_admin(c,x_m3_session)
        r=c.execute('SELECT * FROM iam_sod_conflicts WHERE conflict_code=?',(conflict_code,)).fetchone()
        if not r: raise HTTPException(404,'SoD conflict not found')
        before=dict(r); c.execute("UPDATE iam_sod_conflicts SET status='INACTIVE' WHERE id=?",(r['id'],))
        audit(c,s['user_ref'],'FINANCE_SOD_REVOKE','SOD_CONFLICT',conflict_code,before,{'status':'INACTIVE'})
        return {'status':'REVOKED','conflict_code':conflict_code}
    finally:c.close()


@router.post('/evaluate')
def evaluate(b:EvaluateRequest,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        auth_admin(c,x_m3_session)
        tx,act=validate_tx_action(b.transaction_type,b.action)
        u=c.execute('SELECT * FROM iam_users WHERE username=?',(b.username,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        subjects={limit_subject('USER',b.username)}
        subjects.update(limit_subject('ROLE',r['role_code']) for r in roles_for(c,u['id']))
        ts=now()
        matches=[]
        for r in c.execute("SELECT * FROM iam_approval_limits WHERE status='ACTIVE' AND currency=?",(b.currency.upper(),)):
            if r['role_code'] not in subjects: continue
            d=parse_limit_action(r['action'])
            if d.get('TX')!=tx or d.get('ACT')!=act: continue
            if d.get('OFFICE','*') not in ('*',(b.office_code or '').upper()): continue
            if d.get('COUNTRY','*') not in ('*',(b.country_code or '').upper()): continue
            if d.get('FROM','*')!='*' and ts<d['FROM']: continue
            if d.get('TO','*')!='*' and ts>d['TO']: continue
            x=dict(r); x['dimensions']=d; matches.append(x)
        matches.sort(key=lambda x:int(x['dimensions'].get('LVL','1')))
        qualifying=[x for x in matches if b.amount<=x['amount_limit']]
        if qualifying:
            chosen=qualifying[0]
            return {'decision':'WITHIN_LIMIT','required_level':int(chosen['dimensions']['LVL']),'matched_limit':chosen,'escalation_required':False}
        return {'decision':'ESCALATE','required_level':None,'matched_limit':None,'escalation_required':True,'reason':'AMOUNT_EXCEEDS_AVAILABLE_LIMIT' if matches else 'NO_APPLICABLE_APPROVAL_LIMIT'}
    finally:c.close()


@router.get('/sod-check/{username}')
def sod_check(username:str,x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    c=connect()
    try:
        auth_admin(c,x_m3_session)
        u=c.execute('SELECT * FROM iam_users WHERE username=?',(username,)).fetchone()
        if not u: raise HTTPException(404,'User not found')
        hits=sod_violations(c,u['id'])
        return {'username':username,'clear':not hits,'conflicts':hits,'capabilities':sorted(user_capabilities(c,u['id']))}
    finally:c.close()
