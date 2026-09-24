from fastapi import APIRouter,HTTPException,Header
from pydantic import BaseModel
from typing import Optional
import datetime,json,uuid
from .db import connect,tx,backend_name

router=APIRouter(prefix='/api/v1/gl/hardening',tags=['CLX-006 GL Hardening'])
WRITE={'ADMIN','FINANCE','GL_MANAGER','GL_ACCOUNTANT'}
MANAGE={'ADMIN','FINANCE','GL_MANAGER'}

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def role(x,manage=False):
    r=x.upper(); allowed=MANAGE if manage else WRITE|{'AUDITOR','VIEWER','OPS'}
    if r not in allowed: raise HTTPException(403,'GL hardening access denied')
    return r
def audit(conn,r,action,module,metadata=None):
    conn.execute('INSERT INTO audit_events(event_id,ts,actor_role,actor_scope,action,module,metadata_json) VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),now(),r,'GL',action,'gl:'+module,json.dumps(metadata or {})))
def period_for(conn,date_text):
    p=conn.execute("SELECT p.*,fy.fiscal_year FROM gl_periods p JOIN gl_fiscal_years fy ON fy.id=p.fiscal_year_id WHERE date(?) BETWEEN date(p.start_date) AND date(p.end_date)",(date_text,)).fetchone()
    if not p: raise HTTPException(422,{'code':'NO_ACCOUNTING_PERIOD'})
    return p

class PeriodAction(BaseModel):
    version:int
    reason:Optional[str]=None
class MatchBody(BaseModel):
    statement_ref:str
    voucher_no:str
    amount:float
class RevalueBody(BaseModel):
    period:str='2026-09'
    currency:str
    new_rate:float
    exposure:float
    job_ref:Optional[str]=None

@router.get('/health')
def health():
    return {'project':'M3 NVOCC ERP','baseline':'M3-CLX006-REBRAND-20260923-001','database':backend_name(),'period_control':True,'maker_checker':True,'production_promoted':False,'live_bank_payment_tax_connections':False}

@router.get('/fiscal-years')
def fiscal_years(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); out=[dict(x) for x in c.execute('SELECT * FROM gl_fiscal_years ORDER BY fiscal_year')]; c.close(); return out

@router.get('/periods')
def periods(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); out=[dict(x) for x in c.execute('SELECT p.*,fy.fiscal_year FROM gl_periods p JOIN gl_fiscal_years fy ON fy.id=p.fiscal_year_id ORDER BY fiscal_year,period_no')]; c.close(); return out

@router.post('/periods/{pid}/actions/{action}')
def period_action(pid:int,action:str,b:PeriodAction,x_role:str=Header('VIEWER')):
    r=role(x_role,True); c=connect(); tx(c)
    try:
        p=c.execute('SELECT * FROM gl_periods WHERE id=?',(pid,)).fetchone()
        if not p: raise HTTPException(404,'Period not found')
        if p['version']!=b.version: raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':p['version']})
        a=action.lower()
        if a=='close':
            bad=c.execute("SELECT COUNT(*) n FROM gl_period_close_checks WHERE period_id=? AND status!='PASS'",(pid,)).fetchone()['n']
            count=c.execute('SELECT COUNT(*) n FROM gl_period_close_checks WHERE period_id=?',(pid,)).fetchone()['n']
            if bad or not count: raise HTTPException(422,{'code':'PERIOD_CLOSE_CHECKLIST_INCOMPLETE','failed':bad,'checks':count})
            st='CLOSED'; extra=('closed_at',now())
        elif a=='lock': st='LOCKED'; extra=('locked_at',now())
        elif a=='open': st='OPEN'; extra=(None,None)
        else: raise HTTPException(422,{'code':'UNKNOWN_PERIOD_ACTION'})
        sql='UPDATE gl_periods SET status=?,version=version+1'+(f', {extra[0]}=?' if extra[0] else '')+' WHERE id=? AND version=?'
        args=[st]+([extra[1]] if extra[0] else [])+[pid,b.version]
        q=c.execute(sql,args)
        if q.rowcount!=1: raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT'})
        pp=c.execute('SELECT p.*,fy.fiscal_year FROM gl_periods p JOIN gl_fiscal_years fy ON fy.id=p.fiscal_year_id WHERE p.id=?',(pid,)).fetchone()
        ext=f"PER-{pp['fiscal_year']}-{int(pp['period_no']):02d}"
        gr=c.execute("SELECT id,payload_json FROM gl_records WHERE module='accounting-periods' AND external_ref=?",(ext,)).fetchone()
        if gr:
            payload=json.loads(gr['payload_json']); payload['Status']=st
            c.execute('UPDATE gl_records SET status=?,payload_json=?,version=version+1,updated_at=? WHERE id=?',(st,json.dumps(payload),now(),gr['id']))
        audit(c,r,a.upper(),'period',{'period_id':pid,'reason':b.reason,'status':st}); c.execute('COMMIT')
        return dict(c.execute('SELECT * FROM gl_periods WHERE id=?',(pid,)).fetchone())
    except HTTPException:
        c.execute('ROLLBACK'); raise
    finally: c.close()

@router.get('/period-close/{pid}/checklist')
def checklist(pid:int,x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); rows=[dict(x) for x in c.execute('SELECT * FROM gl_period_close_checks WHERE period_id=? ORDER BY id',(pid,))]; c.close()
    return {'period_id':pid,'all_pass':bool(rows) and all(x['status']=='PASS' for x in rows),'checks':rows}

@router.get('/ar-aging')
def ar_aging(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect()
    q="""SELECT cu.name party,a.currency,ROUND(SUM(a.outstanding),2) total,
    ROUND(SUM(CASE WHEN julianday('2026-09-23')-julianday(a.due_date)<=0 THEN a.outstanding ELSE 0 END),2) current,
    ROUND(SUM(CASE WHEN julianday('2026-09-23')-julianday(a.due_date) BETWEEN 1 AND 30 THEN a.outstanding ELSE 0 END),2) d1_30,
    ROUND(SUM(CASE WHEN julianday('2026-09-23')-julianday(a.due_date) BETWEEN 31 AND 60 THEN a.outstanding ELSE 0 END),2) d31_60,
    ROUND(SUM(CASE WHEN julianday('2026-09-23')-julianday(a.due_date) BETWEEN 61 AND 90 THEN a.outstanding ELSE 0 END),2) d61_90,
    ROUND(SUM(CASE WHEN julianday('2026-09-23')-julianday(a.due_date)>90 THEN a.outstanding ELSE 0 END),2) d90_plus
    FROM gl_ar_open_items a JOIN customers cu ON cu.id=a.customer_id WHERE a.status='OPEN' GROUP BY cu.id,a.currency"""
    rows=[dict(x) for x in c.execute(q)]; c.close(); return rows

@router.get('/ap-aging')
def ap_aging(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect()
    q="""SELECT supplier_name party,currency,ROUND(SUM(outstanding),2) total,
    ROUND(SUM(CASE WHEN julianday('2026-09-23')-julianday(due_date)<=0 THEN outstanding ELSE 0 END),2) current,
    ROUND(SUM(CASE WHEN julianday('2026-09-23')-julianday(due_date) BETWEEN 1 AND 30 THEN outstanding ELSE 0 END),2) d1_30,
    ROUND(SUM(CASE WHEN julianday('2026-09-23')-julianday(due_date) BETWEEN 31 AND 60 THEN outstanding ELSE 0 END),2) d31_60,
    ROUND(SUM(CASE WHEN julianday('2026-09-23')-julianday(due_date) BETWEEN 61 AND 90 THEN outstanding ELSE 0 END),2) d61_90,
    ROUND(SUM(CASE WHEN julianday('2026-09-23')-julianday(due_date)>90 THEN outstanding ELSE 0 END),2) d90_plus
    FROM gl_ap_open_items WHERE status='OPEN' GROUP BY supplier_name,currency"""
    rows=[dict(x) for x in c.execute(q)]; c.close(); return rows

@router.get('/credit-control')
def credit_control(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); rows=[dict(x) for x in c.execute('SELECT cc.*,cu.name customer_name,ROUND(cc.credit_limit-cc.exposure,2) available FROM gl_credit_limits cc JOIN customers cu ON cu.id=cc.customer_id ORDER BY cu.name')]; c.close(); return rows

@router.get('/supplier-payables')
def supplier_payables(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); rows=[dict(x) for x in c.execute("SELECT supplier_name,currency,ROUND(SUM(outstanding),2) outstanding,ROUND(SUM(CASE WHEN date(due_date)<date('2026-09-23') THEN outstanding ELSE 0 END),2) overdue FROM gl_ap_open_items WHERE status='OPEN' GROUP BY supplier_name,currency")]; c.close(); return rows

@router.get('/fx/events')
def fx_events(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); rows=[dict(x) for x in c.execute('SELECT e.*,j.job_ref FROM gl_fx_events e LEFT JOIN jobs j ON j.id=e.job_id ORDER BY e.id')]; c.close(); return rows

@router.post('/fx/revalue')
def revalue(b:RevalueBody,x_role:str=Header('VIEWER')):
    r=role(x_role); c=connect(); tx(c)
    try:
        old=c.execute("SELECT rate FROM gl_fx_rates WHERE currency=? AND base_currency='USD' ORDER BY date(rate_date) DESC LIMIT 1",(b.currency,)).fetchone()
        if not old: raise HTTPException(422,{'code':'FX_RATE_MISSING'})
        p=period_for(c,b.period+'-23'); jid=None
        if b.job_ref:
            j=c.execute('SELECT id FROM jobs WHERE job_ref=?',(b.job_ref,)).fetchone(); jid=j['id'] if j else None
        gl=round(b.exposure*(b.new_rate-float(old['rate'])),2); ref='FXR-'+uuid.uuid4().hex[:8].upper()
        c.execute('INSERT INTO gl_fx_events(event_ref,event_type,period_id,job_id,currency,foreign_amount,old_rate,new_rate,gain_loss,status) VALUES(?,?,?,?,?,?,?,?,?,?)',(ref,'UNREALIZED',p['id'],jid,b.currency,b.exposure,float(old['rate']),b.new_rate,gl,'Calculated'))
        audit(c,r,'REVALUE','fx',{'ref':ref,'gain_loss':gl}); c.execute('COMMIT'); return {'event_ref':ref,'gain_loss':gl,'old_rate':old['rate'],'new_rate':b.new_rate}
    except HTTPException:
        c.execute('ROLLBACK'); raise
    finally: c.close()

@router.get('/bank/unmatched')
def unmatched(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); rows=[dict(x) for x in c.execute('SELECT * FROM gl_bank_statement_items WHERE matched=0 ORDER BY txn_date,id')]; c.close(); return rows

@router.post('/bank/match/manual')
def manual_match(b:MatchBody,x_role:str=Header('VIEWER')):
    r=role(x_role,True); c=connect(); tx(c)
    try:
        si=c.execute('SELECT * FROM gl_bank_statement_items WHERE statement_ref=?',(b.statement_ref,)).fetchone(); v=c.execute('SELECT * FROM gl_vouchers WHERE voucher_no=?',(b.voucher_no,)).fetchone()
        if not si or not v: raise HTTPException(404,'Statement item or voucher not found')
        if si['matched']: raise HTTPException(409,{'code':'BANK_ITEM_ALREADY_MATCHED'})
        if abs(abs(si['amount'])-abs(b.amount))>.01: raise HTTPException(422,{'code':'MATCH_AMOUNT_MISMATCH'})
        if abs(abs(v['total_debit'])-abs(b.amount))>.01: raise HTTPException(422,{'code':'BOOK_AMOUNT_MISMATCH'})
        c.execute('INSERT INTO gl_bank_matches(statement_item_id,voucher_id,match_type,matched_amount,actor_role,ts) VALUES(?,?,?,?,?,?)',(si['id'],v['id'],'MANUAL',b.amount,r,now())); c.execute('UPDATE gl_bank_statement_items SET matched=1 WHERE id=?',(si['id'],))
        audit(c,r,'MANUAL_MATCH','bank',{'statement_ref':b.statement_ref,'voucher_no':b.voucher_no}); c.execute('COMMIT'); return {'matched':True,'statement_ref':b.statement_ref,'voucher_no':b.voucher_no}
    except HTTPException:
        c.execute('ROLLBACK'); raise
    finally: c.close()

@router.post('/bank/match/auto')
def auto_match(x_role:str=Header('VIEWER')):
    r=role(x_role,True); c=connect(); tx(c); matched=[]
    try:
        for si in c.execute('SELECT * FROM gl_bank_statement_items WHERE matched=0 ORDER BY id').fetchall():
            v=c.execute("SELECT * FROM gl_vouchers WHERE status='Posted' AND ABS(total_debit-?)<0.01 ORDER BY id LIMIT 1",(abs(si['amount']),)).fetchone()
            if v:
                c.execute('INSERT OR IGNORE INTO gl_bank_matches(statement_item_id,voucher_id,match_type,matched_amount,actor_role,ts) VALUES(?,?,?,?,?,?)',(si['id'],v['id'],'AUTO',abs(si['amount']),r,now())); c.execute('UPDATE gl_bank_statement_items SET matched=1 WHERE id=?',(si['id'],)); matched.append(si['statement_ref'])
        audit(c,r,'AUTO_MATCH','bank',{'matched':matched}); c.execute('COMMIT'); return {'matched_count':len(matched),'statement_refs':matched}
    finally: c.close()

@router.get('/job-profitability/{job_ref}')
def profitability(job_ref:str,x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); j=c.execute('SELECT id FROM jobs WHERE job_ref=?',(job_ref,)).fetchone()
    if not j: c.close(); raise HTTPException(404,'Unknown job')
    rev=c.execute("SELECT COALESCE(SUM(l.credit-l.debit),0) x FROM gl_voucher_lines l JOIN gl_vouchers v ON v.id=l.voucher_id WHERE v.job_id=? AND v.status='Posted' AND l.account_code LIKE '4%'",(j['id'],)).fetchone()['x']
    cost=c.execute("SELECT COALESCE(SUM(l.debit-l.credit),0) x FROM gl_voucher_lines l JOIN gl_vouchers v ON v.id=l.voucher_id WHERE v.job_id=? AND v.status='Posted' AND l.account_code LIKE '5%'",(j['id'],)).fetchone()['x']
    c.close(); return {'job_ref':job_ref,'revenue':round(rev,2),'cost':round(cost,2),'margin':round(rev-cost,2)}

@router.get('/statements/trial-balance')
def trial_balance(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); rows=[dict(x) for x in c.execute("SELECT a.account_code,a.account_name,a.account_type,ROUND(COALESCE(SUM(CASE WHEN v.status='Posted' THEN l.debit ELSE 0 END),0),2) debit,ROUND(COALESCE(SUM(CASE WHEN v.status='Posted' THEN l.credit ELSE 0 END),0),2) credit,ROUND(COALESCE(SUM(CASE WHEN v.status='Posted' THEN l.debit-l.credit ELSE 0 END),0),2) balance FROM gl_accounts a LEFT JOIN gl_voucher_lines l ON l.account_code=a.account_code LEFT JOIN gl_vouchers v ON v.id=l.voucher_id GROUP BY a.id ORDER BY a.account_code")]; c.close(); return rows

@router.get('/statements/profit-loss')
def profit_loss(x_role:str=Header('AUDITOR')):
    rows=trial_balance(x_role); revenue=round(sum(-x['balance'] for x in rows if x['account_type']=='REVENUE'),2); expenses=round(sum(x['balance'] for x in rows if x['account_type']=='EXPENSE'),2)
    return {'revenue':revenue,'expenses':expenses,'net_profit':round(revenue-expenses,2),'lines':[x for x in rows if x['account_type'] in ('REVENUE','EXPENSE')]}

@router.get('/statements/balance-sheet')
def balance_sheet(x_role:str=Header('AUDITOR')):
    rows=trial_balance(x_role); assets=round(sum(x['balance'] for x in rows if x['account_type']=='ASSET'),2); liabilities=round(sum(-x['balance'] for x in rows if x['account_type']=='LIABILITY'),2)
    return {'assets':assets,'liabilities':liabilities,'retained_result':round(assets-liabilities,2),'lines':[x for x in rows if x['account_type'] in ('ASSET','LIABILITY')]}

@router.get('/gl-detail')
def gl_detail(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); rows=[dict(x) for x in c.execute("SELECT v.voucher_no,v.voucher_date,v.currency,v.exchange_rate,l.line_no,l.account_code,j.job_ref,l.debit,l.credit,l.description FROM gl_voucher_lines l JOIN gl_vouchers v ON v.id=l.voucher_id LEFT JOIN jobs j ON j.id=l.job_id WHERE v.status='Posted' ORDER BY v.voucher_date,v.id,l.line_no")]; c.close(); return rows

@router.get('/subledger-reconciliation')
def subledger_reconciliation(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); out=[]
    for m in ('invoice','bills','receipt','payment'):
        for r in c.execute('SELECT g.*,j.job_ref FROM gl_records g LEFT JOIN jobs j ON j.id=g.job_id WHERE g.module=?',(m,)):
            p=json.loads(r['payload_json']); amt=float(p.get('Amount') or 0)
            links=c.execute('SELECT v.total_debit,v.status FROM gl_source_links l JOIN gl_vouchers v ON v.id=l.voucher_id WHERE l.job_id=? AND l.source_type=? AND l.source_ref=?',(r['job_id'],m,r['external_ref'])).fetchall()
            glamt=sum(float(x['total_debit']) for x in links if x['status']=='Posted')
            out.append({'subledger':m,'source_ref':r['external_ref'],'job_ref':r['job_ref'],'subledger_amount':amt,'gl_amount':glamt,'difference':round(amt-glamt,2),'status':'RECONCILED' if abs(amt-glamt)<.01 else 'REVIEW'})
    c.close(); return out

@router.get('/tax-wht-postings')
def tax_wht(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); rows=[dict(x) for x in c.execute('SELECT t.*,j.job_ref FROM gl_tax_postings t LEFT JOIN jobs j ON j.id=t.job_id ORDER BY t.id')]; c.close(); return rows

@router.get('/month-end/status')
def month_end(x_role:str=Header('AUDITOR')):
    role(x_role); c=connect(); p=c.execute("SELECT * FROM gl_periods WHERE start_date='2026-09-01'").fetchone(); checks=[dict(x) for x in c.execute('SELECT * FROM gl_period_close_checks WHERE period_id=?',(p['id'],))]; unmatched=c.execute('SELECT COUNT(*) n FROM gl_bank_statement_items WHERE matched=0').fetchone()['n']; c.close()
    return {'period':'2026-09','period_status':p['status'],'checklist_pass':all(x['status']=='PASS' for x in checks),'checks':len(checks),'unmatched_bank_items':unmatched}