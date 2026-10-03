from fastapi import APIRouter,HTTPException,Header,Query,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from typing import Any,Optional
from pathlib import Path
import json,hashlib,uuid,datetime,sqlite3
from .db import connect,tx,IntegrityError,backend_name
from .json_recovery import load_json_or_recover_arrays
from .admin import session as iam_session, permission_code as iam_permission_code
from .clx034_smart_approval_fast_track import enforce_gl_action
from .clx044_gl_exact_flow import apply_gl_exact_flow

HERE=Path(__file__).resolve().parent
META, META_RECOVERED = load_json_or_recover_arrays(
    HERE/'gl_meta.json',
    ['setup','transactions','controls','reports'],
)
META = apply_gl_exact_flow(META)
ALL=[dict(x,group='setup') for x in META['setup']]+[dict(x,group='transactions') for x in META['transactions']]+[dict(x,group='controls') for x in META.get('controls',[])]+[dict(x,group='reports') for x in META.get('reports',[])]
MODULES={x['key']:x for x in ALL}
SETUP={x['key'] for x in META['setup']}; TRANSACTIONS={x['key'] for x in META['transactions']}; CONTROLS={x['key'] for x in META.get('controls',[])}; REPORTS={x['key'] for x in META.get('reports',[])}
router=APIRouter(prefix='/api/v1/gl',tags=['General / Administration · Finance & Accounting Setup'])
ROLE_PERMS={
 'ADMIN':set('view create edit approve post reverse cancel reconcile close'.split()),
 'GL_MANAGER':set('view create edit approve post reverse cancel reconcile close'.split()),
 'GL_ACCOUNTANT':set('view create edit post reconcile'.split()),
 'AUDITOR':set('view'.split()),
 'FINANCE':set(),
 'VIEWER':set(),
 'OPS':set(),
 'DOCS':set(),
 'AGENT':set()
}
class CreateBody(BaseModel):
    job_ref:Optional[str]=Field(default=None,pattern=r'^\d{5}$')
    external_ref:Optional[str]=None
    source_type:Optional[str]=None
    source_ref:Optional[str]=None
    fields:dict[str,Any]=Field(default_factory=dict)
class UpdateBody(BaseModel):
    version:int=Field(ge=1)
    fields:dict[str,Any]
class ActionBody(BaseModel):
    version:int=Field(ge=1)
    reason:Optional[str]=None

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def jdump(x):return json.dumps(x,sort_keys=True,separators=(',',':'))
def require_module(m):
    if m not in MODULES: raise HTTPException(404,'Unknown GL module')
def actor(role,action='view',session_token=None,module=None):
    if session_token:
        c=connect()
        try:
            s=iam_session(c,session_token)
            code='GL_VIEW'
            if action=='create': code='JOURNAL_CREATE' if module=='voucher' else 'GL_CREATE'
            elif action=='edit': code='GL_EDIT'
            elif action=='approve': code='JOURNAL_APPROVE'
            elif action=='post': code='GL_POST'
            elif action=='reverse': code='GL_POST'
            elif action=='cancel': code='GL_EDIT'
            elif action=='reconcile': code='GL_EDIT'
            elif action=='close': code='PERIOD_CLOSE'
            if module=='account-integration' and action in {'create','edit'}: code='ACCOUNT_MAPPING_MANAGE'
            if module in {'currency','fx-rates'} and action in {'create','edit'}: code='TAX_CURRENCY_MANAGE'
            if module in {'accounting-periods','fiscal-year'} and action in {'create','edit'}: code='PERIOD_CLOSE'
            if not iam_permission_code(c,s['user_id'],code,s['office_code']): raise HTTPException(403,{'code':'GL_ENTITLEMENT_REQUIRED','permission':code})
            return s['user_ref']
        finally:c.close()
    role=role.upper()
    if role not in ROLE_PERMS: raise HTTPException(403,'Unknown role')
    if action not in ROLE_PERMS[role]: raise HTTPException(403,f'Role cannot {action} GL')
    return role
def serialize(r):
    d=dict(r); d['fields']=json.loads(d.pop('payload_json')); return d
def get_record(conn,module,rid):
    r=conn.execute('''SELECT g.*,j.job_ref FROM gl_records g LEFT JOIN jobs j ON j.id=g.job_id WHERE g.module=? AND g.id=?''',(module,rid)).fetchone()
    if not r: raise HTTPException(404,'GL record not found')
    return r
def audit(conn,role,action,module,record_id=None,job_id=None,before=None,after=None,metadata=None):
    conn.execute('''INSERT INTO audit_events(event_id,ts,actor_role,actor_scope,action,module,transaction_id,job_id,before_json,after_json,metadata_json)
    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(str(uuid.uuid4()),now(),role,'GL',action,'gl:'+module,record_id,job_id,jdump(before) if before is not None else None,jdump(after) if after is not None else None,jdump(metadata or {})))
def job_id(conn,jr):
    if not jr:return None
    r=conn.execute('SELECT id FROM jobs WHERE job_ref=?',(jr,)).fetchone()
    if not r:raise HTTPException(422,{'code':'UNKNOWN_JOB'})
    return r['id']
def _num(v):
    if v in (None,''): return 0.0
    return float(v)

def _line_amount(line,*keys):
    for k in keys:
        if k in line and line[k] not in (None,''): return _num(line[k])
    return 0.0

def validate(module,fields):
    errs=[]
    if module=='opening-balance':
        try:
            if _num(fields.get('Debit'))>0 and _num(fields.get('Credit'))>0: errs.append('OPENING_BALANCE_SIDE_CONFLICT')
        except: errs.append('INVALID_AMOUNT')
    if module=='bank-reconciliation':
        try:
            book=_num(fields.get('Book Balance')); bank=_num(fields.get('Statement Balance') if fields.get('Statement Balance') not in (None,'') else fields.get('Bank Balance'))
            diff=book-bank
            supplied=fields.get('Difference')
            if supplied not in (None,'') and abs(_num(supplied)-diff)>0.01:errs.append('RECONCILIATION_DIFFERENCE_MISMATCH')
            if str(fields.get('Status') or '').lower()=='reconciled' and abs(diff)>0.01:errs.append('RECONCILIATION_NOT_BALANCED')
        except: errs.append('INVALID_RECONCILIATION_AMOUNT')
    if module=='tax-tool':
        try:
            calc=_num(fields.get('Base Amount'))*_num(fields.get('Rate'))/100
            if fields.get('Tax Amount') not in (None,'') and abs(_num(fields.get('Tax Amount'))-calc)>0.01:errs.append('TAX_AMOUNT_MISMATCH')
        except:errs.append('INVALID_TAX_AMOUNT')
    if module in {'invoice','bills','receipt','payment','payment-requisition','credit-note','debit-note','accruals','prepayments','month-end-journals'}:
        try:
            amount=fields.get('Amount')
            if module in {'invoice','bills'}: amount=fields.get('Invoice Amount',amount)
            elif module=='receipt': amount=fields.get('Receipt Amount',amount)
            elif module=='payment': amount=fields.get('Payment Amount',amount)
            if amount not in (None,'') and _num(amount)<=0:errs.append('AMOUNT_MUST_BE_POSITIVE')
        except:errs.append('INVALID_AMOUNT')
    if module=='voucher':
        try:
            lines=fields.get('Account Lines') or []
            if lines:
                debit=sum(_line_amount(x,'Debit (VC)','Debit','Debit(VC)') for x in lines)
                credit=sum(_line_amount(x,'Credit (VC)','Credit','Credit(VC)') for x in lines)
                if debit<=0 or credit<=0: errs.append('VOUCHER_DEBIT_CREDIT_REQUIRED')
                if abs(debit-credit)>0.01: errs.append('UNBALANCED_VOUCHER')
                for x in lines:
                    if not x.get('Account Code'): errs.append('VOUCHER_LINE_ACCOUNT_REQUIRED'); break
            else:
                amt=_num(fields.get('Amount'))
                if amt<=0:errs.append('VOUCHER_AMOUNT_MUST_BE_POSITIVE')
                if not fields.get('Debit Account') or not fields.get('Credit Account'):errs.append('VOUCHER_ACCOUNTS_REQUIRED')
                if fields.get('Debit Account')==fields.get('Credit Account'):errs.append('VOUCHER_ACCOUNTS_MUST_DIFFER')
        except:errs.append('INVALID_VOUCHER_AMOUNT')
    if module in {'invoice','bills'}:
        try:
            inv=_num(fields.get('Invoice Amount')); tax=_num(fields.get('Tax Amount')); net=_num(fields.get('Net Amount'))
            if fields.get('Net Amount') not in (None,'') and abs((inv+tax)-net)>0.01: errs.append('INVOICE_NET_AMOUNT_MISMATCH')
        except: errs.append('INVALID_INVOICE_TOTAL')
    if module in {'receipt','payment'}:
        try:
            gross=_num(fields.get('Receipt Amount') if module=='receipt' else fields.get('Payment Amount'))
            inv_adj=_num(fields.get('Inv Adj Amount')); set_adj=_num(fields.get('Set Adj Amount')); net=_num(fields.get('Net Amount'))
            if fields.get('Net Amount') not in (None,'') and abs((gross+inv_adj+set_adj)-net)>0.01: errs.append('NET_AMOUNT_MISMATCH')
        except: errs.append('INVALID_NET_AMOUNT')
    if module=='cash-denomination-record':
        try:
            total=_num(fields.get('Total')); balance=_num(fields.get('Balance'))
            if fields.get('Difference') not in (None,'') and abs(_num(fields.get('Difference'))-(total-balance))>0.01: errs.append('CASH_DENOMINATION_DIFFERENCE_MISMATCH')
        except: errs.append('INVALID_CASH_DENOMINATION')
    return errs

def fiscal_period(conn,date_text):
    r=conn.execute("SELECT p.*,fy.fiscal_year FROM gl_periods p JOIN gl_fiscal_years fy ON fy.id=p.fiscal_year_id WHERE date(?) BETWEEN date(p.start_date) AND date(p.end_date)",(date_text,)).fetchone()
    if not r: raise HTTPException(422,{'code':'NO_ACCOUNTING_PERIOD','date':date_text})
    return r

def assert_period_postable(conn,date_text):
    p=fiscal_period(conn,date_text)
    if p['status']!='OPEN': raise HTTPException(422,{'code':'ACCOUNTING_PERIOD_NOT_OPEN','period':p['period_no'],'status':p['status']})
    ctl=conn.execute('SELECT earliest_posting_date FROM gl_backdate_controls WHERE period_id=? AND active=1',(p['id'],)).fetchone()
    if ctl and date_text < ctl['earliest_posting_date']:
        raise HTTPException(422,{'code':'BACKDATE_LIMIT_EXCEEDED','date':date_text,'earliest_posting_date':ctl['earliest_posting_date'],'period':p['period_no']})
    return p

def approval_rule(conn,vtype,amount):
    return conn.execute("SELECT * FROM gl_approval_rules WHERE active=1 AND voucher_type IN (?, '*') AND ?>=min_amount AND (max_amount IS NULL OR ?<=max_amount) ORDER BY CASE WHEN voucher_type=? THEN 0 ELSE 1 END,min_amount DESC LIMIT 1",(vtype,amount,amount,vtype)).fetchone()

def approval_count(conn,vid):
    return conn.execute("SELECT COUNT(*) n FROM gl_approval_events WHERE voucher_id=? AND decision='APPROVED'",(vid,)).fetchone()['n']

def fx_rate(conn,currency,date_text):
    if currency=='USD': return 1.0
    r=conn.execute("SELECT rate FROM gl_fx_rates WHERE currency=? AND base_currency='USD' AND date(rate_date)<=date(?) AND status='ACTIVE' ORDER BY date(rate_date) DESC LIMIT 1",(currency,date_text)).fetchone()
    if not r: raise HTTPException(422,{'code':'FX_RATE_REQUIRED','currency':currency,'date':date_text})
    return float(r['rate'])

def next_voucher(conn,vtype):
    r=conn.execute('SELECT * FROM gl_sequences WHERE voucher_type=?',(vtype,)).fetchone()
    if not r:raise HTTPException(422,{'code':'UNKNOWN_VOUCHER_TYPE'})
    no=f"{r['prefix']}-2026-{r['next_number']:06d}"
    conn.execute('UPDATE gl_sequences SET next_number=next_number+1 WHERE voucher_type=?',(vtype,))
    return no

def build_voucher(conn,record_id,fields,jid,source_type,source_ref,status='Draft',maker_role='GL_ACCOUNTANT'):
    vtype=str(fields.get('Voucher Type') or 'JV').upper(); vno=fields.get('Voucher No.') or next_voucher(conn,vtype)
    lines=fields.get('Account Lines') or []
    curr=fields.get('Currency') or 'USD'; vdate=fields.get('Date') or '2026-09-23'
    rate=_num(fields.get('Exchange Rate')) or fx_rate(conn,curr,vdate)
    if lines:
        debit_total=sum(_line_amount(x,'Debit (VC)','Debit','Debit(VC)') for x in lines)
        credit_total=sum(_line_amount(x,'Credit (VC)','Credit','Credit(VC)') for x in lines)
        amount=max(debit_total,credit_total)
    else:
        amount=_num(fields.get('Amount')); debit_total=credit_total=amount
    base_debit=round(debit_total*rate,2); base_credit=round(credit_total*rate,2)
    cur=conn.execute('''INSERT INTO gl_vouchers(gl_record_id,voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,total_debit,total_credit,version,created_at,maker_role,exchange_rate,base_currency,base_total_debit,base_total_credit)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?)''',(record_id,vno,vtype,vdate,curr,status,source_type,source_ref,jid,debit_total,credit_total,now(),maker_role,rate,'USD',base_debit,base_credit))
    vid=cur.lastrowid
    if lines:
        for i,line in enumerate(lines,1):
            account=line.get('Account Code'); debit=_line_amount(line,'Debit (VC)','Debit','Debit(VC)'); credit=_line_amount(line,'Credit (VC)','Credit','Credit(VC)')
            desc=line.get('Narration') or line.get('Particular') or fields.get('Narration') or source_ref
            conn.execute('INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)',(vid,i,account,debit,credit,desc,jid))
    else:
        debit=fields.get('Debit Account'); credit=fields.get('Credit Account')
        conn.execute('INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)',(vid,1,debit,amount,0,fields.get('Narration') or source_ref,jid))
        conn.execute('INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)',(vid,2,credit,0,amount,fields.get('Narration') or source_ref,jid))
    conn.execute('UPDATE gl_records SET external_ref=?,payload_json=json_set(payload_json,\'$."Voucher No."\',?) WHERE id=?',(vno,vno,record_id))
    conn.execute('INSERT INTO gl_source_links(gl_record_id,voucher_id,source_type,source_ref,job_id) VALUES(?,?,?,?,?)',(record_id,vid,source_type or 'MANUAL',source_ref or vno,jid))
    return vid,vno

def voucher_for(conn,rid):return conn.execute('SELECT * FROM gl_vouchers WHERE gl_record_id=?',(rid,)).fetchone()
def voucher_balanced(conn,vid):
    r=conn.execute('SELECT COALESCE(SUM(debit),0) d,COALESCE(SUM(credit),0) c FROM gl_voucher_lines WHERE voucher_id=?',(vid,)).fetchone()
    return abs(r['d']-r['c'])<0.0001 and r['d']>0,r['d'],r['c']

@router.get('/health')
def health():return {'project':'M3 NVOCC ERP','baseline':'M3-CLX006-REBRAND-20260923-001','module':'GENERAL / ADMINISTRATION · FINANCE & ACCOUNTING SETUP','database':backend_name(),'production_promoted':False,'live_integrations':False}
@router.get('/modules')
def modules():return ALL
@router.get('/trial-balance')
def trial_balance(x_role:str=Header('VIEWER'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    actor(x_role,'view',x_m3_session); conn=connect(); rows=[dict(r) for r in conn.execute('''SELECT a.account_code,a.account_name,a.account_type,ROUND(COALESCE(SUM(l.debit),0),2) debit,ROUND(COALESCE(SUM(l.credit),0),2) credit,ROUND(COALESCE(SUM(l.debit-l.credit),0),2) balance FROM gl_accounts a LEFT JOIN gl_voucher_lines l ON l.account_code=a.account_code LEFT JOIN gl_vouchers v ON v.id=l.voucher_id AND v.status IN ('Posted','Approved') GROUP BY a.id ORDER BY a.account_code''')]; conn.close(); return rows
@router.get('/job/{job_ref}/links')
def job_links(job_ref:str,x_role:str=Header('VIEWER'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    actor(x_role,'view',x_m3_session); conn=connect(); j=conn.execute('SELECT id,booking_id FROM jobs WHERE job_ref=?',(job_ref,)).fetchone()
    if not j: conn.close(); raise HTTPException(404,'Unknown job')
    rows=[dict(r) for r in conn.execute('''SELECT l.*,g.module,g.external_ref,v.voucher_no,v.status voucher_status FROM gl_source_links l LEFT JOIN gl_records g ON g.id=l.gl_record_id LEFT JOIN gl_vouchers v ON v.id=l.voucher_id WHERE l.job_id=? ORDER BY l.id''',(j['id'],))]
    ops=[dict(r) for r in conn.execute("SELECT module,external_ref,status FROM transaction_records WHERE job_id=? AND module IN ('soa','agent-receipt-pay','detention-collection','storage-cost','booking') ORDER BY module",(j['id'],))]
    conn.close();return {'job_ref':job_ref,'count':len(rows),'links':rows,'operational_sources':ops}
@router.get('/voucher/{rid}/lines')
def voucher_lines(rid:int,x_role:str=Header('VIEWER'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    actor(x_role,'view',x_m3_session,'voucher'); conn=connect(); v=voucher_for(conn,rid)
    if not v:conn.close();raise HTTPException(404,'Voucher backing record not found')
    rows=[dict(r) for r in conn.execute('SELECT * FROM gl_voucher_lines WHERE voucher_id=? ORDER BY line_no',(v['id'],))]; ok,d,c=voucher_balanced(conn,v['id']); out={'voucher':dict(v),'balanced':ok,'debit':d,'credit':c,'lines':rows};conn.close();return out

@router.get('/{module}')
def list_records(module:str,job_ref:Optional[str]=None,status:Optional[str]=None,q:Optional[str]=None,x_role:str=Header('VIEWER'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_module(module);actor(x_role,'view',x_m3_session,module);conn=connect();sql='''SELECT g.*,j.job_ref FROM gl_records g LEFT JOIN jobs j ON j.id=g.job_id WHERE g.module=?''';args=[module]
    if job_ref:sql+=' AND j.job_ref=?';args.append(job_ref)
    if status:sql+=' AND g.status=?';args.append(status)
    sql+=' ORDER BY g.id';out=[serialize(r) for r in conn.execute(sql,args)];conn.close()
    if q:out=[r for r in out if q.lower() in json.dumps(r).lower()]
    return {'module':module,'count':len(out),'records':out}
@router.get('/{module}/{rid}')
def one(module:str,rid:int,x_role:str=Header('VIEWER'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_module(module);actor(x_role,'view',x_m3_session,module);conn=connect();r=get_record(conn,module,rid);out=serialize(r);conn.close();return out
@router.post('/{module}',status_code=201)
async def create(module:str,body:CreateBody,request:Request,x_role:str=Header('VIEWER'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session'),idempotency_key:Optional[str]=Header(None,alias='Idempotency-Key')):
    require_module(module);role=actor(x_role,'create',x_m3_session,module);raw=await request.body();rh=hashlib.sha256(raw).hexdigest();conn=connect();tx(conn)
    try:
        if idempotency_key:
            prior=conn.execute('SELECT * FROM idempotency_keys WHERE actor_role=? AND idem_key=?',(role,'GL:'+idempotency_key)).fetchone()
            if prior:
                if prior['request_hash']!=rh:raise HTTPException(409,'IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST')
                conn.execute('COMMIT');return JSONResponse(json.loads(prior['response_json']),status_code=prior['status_code'])
        errs=validate(module,body.fields)
        if errs:raise HTTPException(422,{'codes':errs})
        jid=job_id(conn,body.job_ref); ext=body.external_ref or f"CLX-GL-{module.upper()}-{uuid.uuid4().hex[:10].upper()}"; status=str(body.fields.get('Status') or 'Draft')
        if conn.execute('SELECT 1 FROM gl_records WHERE module=? AND external_ref=?',(module,ext)).fetchone():raise HTTPException(409,'DUPLICATE_GL_RECORD')
        payload=dict(body.fields); cur=conn.execute('INSERT INTO gl_records(module,external_ref,job_id,source_type,source_ref,status,version,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?,?)',(module,ext,jid,body.source_type,body.source_ref,status,json.dumps(payload),now(),now()));rid=cur.lastrowid
        if module=='voucher':build_voucher(conn,rid,payload,jid,body.source_type,body.source_ref,status,maker_role=role)
        after=serialize(get_record(conn,module,rid));audit(conn,role,'CREATE',module,rid,jid,None,after,{'source_type':body.source_type,'source_ref':body.source_ref})
        if idempotency_key:conn.execute('INSERT INTO idempotency_keys(actor_role,idem_key,request_hash,response_json,status_code,created_at) VALUES(?,?,?,?,201,?)',(role,'GL:'+idempotency_key,rh,json.dumps(after),now()))
        conn.execute('COMMIT');return JSONResponse(after,status_code=201)
    except HTTPException:conn.execute('ROLLBACK');raise
    except IntegrityError as e:conn.execute('ROLLBACK');raise HTTPException(409,'GL_INTEGRITY_CONFLICT')
    finally:conn.close()
@router.put('/{module}/{rid}')
def update(module:str,rid:int,body:UpdateBody,x_role:str=Header('VIEWER'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_module(module);role=actor(x_role,'edit',x_m3_session,module);conn=connect();tx(conn)
    try:
        r=get_record(conn,module,rid);before=serialize(r)
        if r['version']!=body.version:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
        p=json.loads(r['payload_json']);p.update(body.fields);errs=validate(module,p)
        if errs:raise HTTPException(422,{'codes':errs})
        st=str(p.get('Status') or r['status']);cur=conn.execute('UPDATE gl_records SET payload_json=?,status=?,version=version+1,updated_at=? WHERE id=? AND version=?',(json.dumps(p),st,now(),rid,body.version))
        if cur.rowcount!=1:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT'})
        after=serialize(get_record(conn,module,rid));audit(conn,role,'UPDATE',module,rid,r['job_id'],before,after,{})
        conn.execute('COMMIT');return after
    except HTTPException:conn.execute('ROLLBACK');raise
    finally:conn.close()
@router.post('/{module}/{rid}/actions/{action}')
def action(module:str,rid:int,action:str,body:ActionBody,x_role:str=Header('VIEWER'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    require_module(module);action=action.lower();role=actor(x_role,action,x_m3_session,module)
    if action in {'approve','post','reverse','close'}: enforce_gl_action(module,rid,action,x_m3_session,body.version,body.reason)
    conn=connect();tx(conn)
    try:
        r=get_record(conn,module,rid);before=serialize(r)
        if r['version']!=body.version:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
        p=json.loads(r['payload_json']);st=r['status'];meta={'reason':body.reason}
        if action=='approve':
            st='Approved';p['Status']=st
            if module=='voucher':
                v=voucher_for(conn,rid)
                if not v: raise HTTPException(422,{'code':'VOUCHER_ENGINE_RECORD_MISSING'})
                if v['maker_role'] and v['maker_role']==role: raise HTTPException(422,{'code':'MAKER_CANNOT_APPROVE_OWN_VOUCHER'})
                rule=approval_rule(conn,v['voucher_type'],float(v['total_debit'])); needed=int(rule['required_levels']) if rule else 1
                done=approval_count(conn,v['id'])
                if done>=needed: raise HTTPException(422,{'code':'APPROVAL_ALREADY_COMPLETE'})
                level=done+1
                conn.execute('INSERT INTO gl_approval_events(voucher_id,level,actor_role,decision,ts,comment) VALUES(?,?,?,?,?,?)',(v['id'],level,role,'APPROVED',now(),body.reason))
                if level>=needed: conn.execute("UPDATE gl_vouchers SET status='Approved',approved_by=?,approved_at=?,version=version+1 WHERE id=?",(role,now(),v['id']))
                else: st='Pending Approval';p['Status']=st
                meta.update({'approval_level':level,'required_levels':needed})
        elif action=='cancel':st='Cancelled';p['Status']=st
        elif action=='reconcile':
            if module!='bank-reconciliation':raise HTTPException(422,{'code':'RECONCILE_ONLY_BANK_RECONCILIATION'})
            errs=validate(module,{**p,'Status':'Reconciled'})
            if errs:raise HTTPException(422,{'codes':errs})
            st='Reconciled';p['Status']=st
        elif action=='post':
            if module!='voucher':raise HTTPException(422,{'code':'POST_ONLY_VOUCHER'})
            v=voucher_for(conn,rid)
            if not v:raise HTTPException(422,{'code':'VOUCHER_ENGINE_RECORD_MISSING'})
            ok,d,c=voucher_balanced(conn,v['id'])
            if not ok:raise HTTPException(422,{'code':'UNBALANCED_VOUCHER','debit':d,'credit':c})
            assert_period_postable(conn,v['voucher_date'])
            rule=approval_rule(conn,v['voucher_type'],float(v['total_debit'])); needed=int(rule['required_levels']) if rule else 1
            done=approval_count(conn,v['id'])
            if v['status']!='Approved' or done<needed: raise HTTPException(422,{'code':'APPROVAL_REQUIRED_BEFORE_POSTING','approvals':done,'required':needed})
            conn.execute("UPDATE gl_vouchers SET status='Posted',posted_at=?,version=version+1 WHERE id=?",(now(),v['id']));st='Posted';p['Status']=st;meta.update({'balanced':True,'period_open':True,'approvals':done})
        elif action=='close':
            if module!='accounting-periods':raise HTTPException(422,{'code':'CLOSE_ONLY_ACCOUNTING_PERIOD'})
            period_no=int(p.get('Period') or 0)
            if period_no<1 or period_no>12:raise HTTPException(422,{'code':'ACCOUNTING_PERIOD_NUMBER_REQUIRED'})
            period=conn.execute('SELECT * FROM gl_periods WHERE period_no=? ORDER BY id DESC LIMIT 1',(period_no,)).fetchone()
            if not period:raise HTTPException(422,{'code':'ACCOUNTING_PERIOD_ENGINE_RECORD_MISSING'})
            conn.execute("UPDATE gl_periods SET status='CLOSED' WHERE id=?",(period['id'],));st='CLOSED';p['Status']=st;meta.update({'period_no':period_no,'period_closed':True})
        elif action=='reverse':
            if module!='voucher':raise HTTPException(422,{'code':'REVERSE_ONLY_VOUCHER'})
            v=voucher_for(conn,rid)
            if not v or v['status']!='Posted':raise HTTPException(422,{'code':'ONLY_POSTED_VOUCHER_CAN_REVERSE'})
            assert_period_postable(conn,v['voucher_date'])
            rvno=next_voucher(conn,'RV'); cur=conn.execute('''INSERT INTO gl_vouchers(gl_record_id,voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,total_debit,total_credit,version,reversal_of,posted_at,created_at) VALUES(NULL,?,?,?,?,?,?,?,?,?,?,1,?,?,?)''',(rvno,'RV',v['voucher_date'],v['currency'],'Posted','REVERSAL',v['voucher_no'],v['job_id'],v['total_credit'],v['total_debit'],v['id'],now(),now()));rvid=cur.lastrowid
            for line in conn.execute('SELECT * FROM gl_voucher_lines WHERE voucher_id=? ORDER BY line_no',(v['id'],)):
                conn.execute('INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)',(rvid,line['line_no'],line['account_code'],line['credit'],line['debit'],'Reversal of '+v['voucher_no'],line['job_id']))
            conn.execute('UPDATE gl_vouchers SET status=\'Reversed\',version=version+1 WHERE id=?',(v['id'],));st='Reversed';p['Status']=st;meta['reversal_voucher']=rvno
        else:raise HTTPException(422,{'code':'UNKNOWN_GL_ACTION'})
        cur=conn.execute('UPDATE gl_records SET status=?,payload_json=?,version=version+1,updated_at=? WHERE id=? AND version=?',(st,json.dumps(p),now(),rid,body.version))
        if cur.rowcount!=1:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT'})
        after=serialize(get_record(conn,module,rid));audit(conn,role,action.upper(),module,rid,r['job_id'],before,after,meta);conn.execute('COMMIT');return {'ok':True,'record':after,'meta':meta}
    except HTTPException:conn.execute('ROLLBACK');raise
    finally:conn.close()