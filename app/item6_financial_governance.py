import os, uuid
from fastapi import HTTPException

from .admin import session as iam_session, roles_for as iam_roles_for
from .db import IntegrityError


IMMUTABLE_FINANCIAL_STATES={
    'APPROVED','POSTED','REVERSED','CANCELLED','CLOSED'
}
APPROVAL_ROLE_MAP={
    'FINANCE_MANAGER':{'FINANCE_MANAGER','GL_MANAGER'},
    'CFO':{'CFO'},
    'EXTERNAL_ADVISOR':{'EXTERNAL_ADVISOR'},
    'TS_BRANCH_MANAGER':{'TS_BRANCH_MANAGER'},
    'TS_FINANCE':{'TS_FINANCE','FINANCE'},
}


def enabled():
    return os.getenv('M3_ITEM6_FINANCIAL_GOVERNANCE_ENABLED','false').lower()=='true'


def enforce_sensitive_session(action, session_token):
    if enabled() and str(action).lower() in {'approve','post','reverse','close'} and not session_token:
        raise HTTPException(401,{'code':'SESSION_REQUIRED_FOR_GOVERNED_FINANCIAL_ACTION'})


def enforce_update_guard(module, is_transaction, status, session_token):
    if not enabled():
        return
    if is_transaction and not session_token:
        raise HTTPException(401,{'code':'SESSION_REQUIRED_FOR_FINANCIAL_MUTATION'})
    state=str(status or '').upper()
    if state in IMMUTABLE_FINANCIAL_STATES:
        code='CRT_REQUIRED_AFTER_APPROVAL' if state in {'APPROVED','POSTED'} else 'FINANCIAL_RECORD_IMMUTABLE'
        raise HTTPException(409,{'code':code,'status':status,'module':module})


def session_context(conn, token):
    if not token:
        return {'user_ref':None,'office_code':None,'roles':[],'country_code':None,'organization_code':None}
    s=iam_session(conn,token)
    roles=[r['role_code'] for r in iam_roles_for(conn,s['user_id'])]
    row=conn.execute("""SELECT c.country_code,o2.org_code organization_code
      FROM iam_offices o
      LEFT JOIN iam_countries c ON c.id=o.country_id
      LEFT JOIN iam_organizations o2 ON o2.id=o.organization_id
      WHERE o.id=?""",(s['home_office_id'],)).fetchone()
    return {
        'user_ref':s['user_ref'],
        'office_code':s['office_code'],
        'roles':roles,
        'country_code':row['country_code'] if row else None,
        'organization_code':row['organization_code'] if row else None,
    }


def required_period_roles(period_status, flow):
    req=set()
    state=str(period_status or '').upper()
    if state=='CLOSED':
        req|={'FINANCE_MANAGER','CFO'}
    elif state=='LOCKED':
        req|={'CFO','EXTERNAL_ADVISOR'}
    elif state!='OPEN':
        return None
    if str(flow or '').upper() in {'TS','TRANSSHIPMENT'}:
        req|={'TS_BRANCH_MANAGER','TS_FINANCE'}
    return req


def approval_roles(conn, period_id, voucher_id, action):
    return {r['approval_role'] for r in conn.execute(
        "SELECT approval_role FROM gl_period_override_approvals WHERE period_id=? AND voucher_id=? AND action=? AND status='APPROVED'",
        (period_id,voucher_id,str(action).upper())
    )}


def ensure_period_governance(conn, period, voucher_id, action, flow=None):
    req=required_period_roles(period['status'],flow)
    if req is None:
        raise HTTPException(422,{'code':'ACCOUNTING_PERIOD_NOT_POSTABLE','period':period['period_no'],'status':period['status']})
    if not req:
        return {'period_mode':'OPEN','approvals':[]}
    got=approval_roles(conn,period['id'],voucher_id,action)
    missing=sorted(req-got)
    if missing:
        code='TAX_FILED_PERIOD_APPROVAL_REQUIRED' if str(period['status']).upper()=='LOCKED' else 'CLOSED_PERIOD_APPROVAL_REQUIRED'
        raise HTTPException(422,{'code':code,'period':period['period_no'],'status':period['status'],'missing_roles':missing})
    mode='TAX_FILED_CONTROLLED' if str(period['status']).upper()=='LOCKED' else 'CLOSED_CONTROLLED'
    return {'period_mode':mode,'approvals':sorted(got)}


def validate_period_approval_role(ctx, approval_role):
    role=str(approval_role or '').upper()
    if role not in APPROVAL_ROLE_MAP:
        raise HTTPException(422,{'code':'UNKNOWN_PERIOD_APPROVAL_ROLE'})
    if not (set(ctx.get('roles') or []) & APPROVAL_ROLE_MAP[role]):
        raise HTTPException(403,{'code':'PERIOD_APPROVAL_ROLE_REQUIRED','approval_role':role})
    return role


def record_period_approval(conn, now_func, period_id, voucher_id, action, approval_role, reason, ctx):
    role=validate_period_approval_role(ctx,approval_role)
    if str(action).upper() not in {'POST','REVERSE'}:
        raise HTTPException(422,{'code':'UNKNOWN_PERIOD_APPROVAL_ACTION'})
    voucher=conn.execute('SELECT * FROM gl_vouchers WHERE id=?',(voucher_id,)).fetchone()
    period=conn.execute('SELECT * FROM gl_periods WHERE id=?',(period_id,)).fetchone()
    if not voucher or not period:
        raise HTTPException(404,{'code':'PERIOD_OR_VOUCHER_NOT_FOUND'})
    if voucher['maker_role'] and voucher['maker_role']==ctx.get('user_ref'):
        raise HTTPException(409,{'code':'MAKER_SELF_ACTION_BLOCKED'})
    ref='PGA-'+uuid.uuid4().hex[:12].upper()
    try:
        conn.execute("""INSERT INTO gl_period_override_approvals
          (approval_ref,period_id,voucher_id,action,approval_role,approver_user_ref,approver_office_code,reason,status,created_at)
          VALUES(?,?,?,?,?,?,?,?, 'APPROVED',?)""",
          (ref,period_id,voucher_id,str(action).upper(),role,ctx['user_ref'],ctx['office_code'],reason,now_func()))
    except IntegrityError:
        raise HTTPException(409,{'code':'PERIOD_APPROVAL_ALREADY_EXISTS','approval_role':role})
    return {
        'approval_ref':ref,'period_id':period_id,'voucher_id':voucher_id,
        'action':str(action).upper(),'approval_role':role,'status':'APPROVED',
        'job_id':voucher['job_id']
    }
