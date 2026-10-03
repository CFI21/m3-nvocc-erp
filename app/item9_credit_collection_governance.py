import os, json, uuid, datetime
from fastapi import HTTPException
from .admin import session as iam_session, roles_for as iam_roles_for

def enabled():
    return os.getenv('M3_ITEM9_CREDIT_COLLECTION_GOVERNANCE_ENABLED','false').lower()=='true'

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def refresh_credit_exposure(conn,customer_id,currency='USD'):
    exposure=conn.execute("""SELECT COALESCE(SUM(outstanding),0) x FROM gl_ar_open_items
      WHERE customer_id=? AND currency=? AND status='OPEN'""",(customer_id,currency)).fetchone()['x']
    row=conn.execute('SELECT * FROM gl_credit_limits WHERE customer_id=?',(customer_id,)).fetchone()
    if not row: raise HTTPException(422,{'code':'CREDIT_LIMIT_NOT_CONFIGURED'})
    hold=1 if float(exposure)>float(row['credit_limit'])+0.005 else 0
    conn.execute('UPDATE gl_credit_limits SET exposure=?,on_hold=?,version=version+1 WHERE id=?',(float(exposure),hold,row['id']))
    return {'customer_id':customer_id,'currency':currency,'credit_limit':float(row['credit_limit']),'exposure':float(exposure),'available':round(float(row['credit_limit'])-float(exposure),2),'on_hold':hold}

def session_context(conn,token):
    if not token: raise HTTPException(401,{'code':'SESSION_REQUIRED_FOR_CREDIT_GOVERNANCE'})
    s=iam_session(conn,token)
    roles=[r['role_code'] for r in iam_roles_for(conn,s['user_id'])]
    return {'user_ref':s['user_ref'],'user_id':s['user_id'],'office_code':s['office_code'],'roles':roles}

def approval_limit(conn,roles,currency,action='CREDIT_OVERRIDE'):
    limits=[float(r['amount_limit']) for r in conn.execute("SELECT * FROM iam_approval_limits WHERE status='ACTIVE' AND currency=? AND action=?",(currency,action)) if r['role_code'] in roles]
    return max(limits) if limits else 0.0

def validate_override_request(conn,ctx,customer_id,amount,currency,reason,expires_at):
    if not reason: raise HTTPException(422,{'code':'CREDIT_OVERRIDE_REASON_REQUIRED'})
    if not expires_at: raise HTTPException(422,{'code':'CREDIT_OVERRIDE_EXPIRY_REQUIRED'})
    limit=approval_limit(conn,ctx['roles'],currency)
    if amount<=0 or amount>limit+0.005:
        raise HTTPException(403,{'code':'CREDIT_OVERRIDE_ABOVE_AUTHORITY','authority_limit':limit,'requested':amount})
    return {'customer_id':customer_id,'amount':float(amount),'currency':currency,'reason':reason,'expires_at':expires_at,'requested_by':ctx['user_ref']}

def effective_credit(conn,customer_id,currency='USD'):
    snap=refresh_credit_exposure(conn,customer_id,currency)
    ts=now()
    ov=conn.execute("""SELECT * FROM credit_override_events WHERE customer_id=? AND currency=? AND status='APPROVED'
      AND expires_at>=? ORDER BY id DESC LIMIT 1""",(customer_id,currency,ts)).fetchone()
    extra=float(ov['amount']) if ov else 0.0
    available=round(snap['credit_limit']+extra-snap['exposure'],2)
    return {**snap,'override_amount':extra,'effective_available':available,'effective_hold':1 if available<0 else 0}

def ensure_credit_available(conn,customer_id,currency,requested_amount):
    state=effective_credit(conn,customer_id,currency)
    if state['effective_hold'] or requested_amount>state['effective_available']+0.005:
        raise HTTPException(422,{'code':'CREDIT_HOLD_ACTIVE','available':state['effective_available'],'requested':requested_amount})
    return state

def approve_override(conn,ctx,override_id):
    row=conn.execute('SELECT * FROM credit_override_events WHERE id=?',(override_id,)).fetchone()
    if not row: raise HTTPException(404,{'code':'CREDIT_OVERRIDE_NOT_FOUND'})
    if row['requested_by']==ctx['user_ref']: raise HTTPException(409,{'code':'MAKER_CANNOT_APPROVE_OWN_CREDIT_OVERRIDE'})
    if row['status']!='PENDING': raise HTTPException(409,{'code':'CREDIT_OVERRIDE_NOT_PENDING'})
    lim=approval_limit(conn,ctx['roles'],row['currency'])
    if float(row['amount'])>lim+0.005: raise HTTPException(403,{'code':'CREDIT_OVERRIDE_ABOVE_AUTHORITY'})
    conn.execute("UPDATE credit_override_events SET status='APPROVED',approved_by=?,approved_at=? WHERE id=?",(ctx['user_ref'],now(),override_id))
    return dict(conn.execute('SELECT * FROM credit_override_events WHERE id=?',(override_id,)).fetchone())

def validate_collection_metadata(fields):
    forbidden={'Outstanding','Exposure','Credit Limit','Available','Principal','Balance'}
    bad=[k for k in forbidden if k in fields]
    if bad: raise HTTPException(422,{'code':'COLLECTION_METADATA_CANNOT_CHANGE_PRINCIPAL','fields':sorted(bad)})
    return True

def dunning_level(days_overdue):
    d=int(days_overdue or 0)
    if d<=0:return 0
    if d<=30:return 1
    if d<=60:return 2
    if d<=90:return 3
    return 4
