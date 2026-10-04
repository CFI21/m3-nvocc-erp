import os
from fastapi import HTTPException

REQUIRED_CLOSE_CHECKS={'SUBLEDGER','BANK','AR_AP','FX','TAX','JOBS','TB','ACCRUALS','PREPAYMENTS'}

def enabled():
    return os.getenv('M3_ITEM11_PERIOD_REPORTING_GOVERNANCE_ENABLED','false').lower()=='true'

def close_check_status(conn,period_id):
    rows=[dict(r) for r in conn.execute("SELECT * FROM gl_period_close_checks WHERE period_id=? ORDER BY id",(period_id,))]
    by={str(r['check_code']).upper():r for r in rows}
    missing=sorted(REQUIRED_CLOSE_CHECKS-set(by))
    failed=sorted(k for k,v in by.items() if k in REQUIRED_CLOSE_CHECKS and str(v['status']).upper()!='PASS')
    return rows,missing,failed

def posted_trial_balance(conn):
    rows=[dict(r) for r in conn.execute("""SELECT a.account_code,a.account_name,a.account_type,
      ROUND(COALESCE(SUM(CASE WHEN v.status='Posted' THEN l.debit ELSE 0 END),0),2) debit,
      ROUND(COALESCE(SUM(CASE WHEN v.status='Posted' THEN l.credit ELSE 0 END),0),2) credit,
      ROUND(COALESCE(SUM(CASE WHEN v.status='Posted' THEN l.debit-l.credit ELSE 0 END),0),2) balance
      FROM gl_accounts a
      LEFT JOIN gl_voucher_lines l ON l.account_code=a.account_code
      LEFT JOIN gl_vouchers v ON v.id=l.voucher_id
      GROUP BY a.id ORDER BY a.account_code""")]
    td=round(sum(float(x['debit'] or 0) for x in rows),2)
    tc=round(sum(float(x['credit'] or 0) for x in rows),2)
    return {'rows':rows,'total_debit':td,'total_credit':tc,'balanced':abs(td-tc)<0.01}

def period_close_blockers(conn,period_id):
    period=conn.execute('SELECT * FROM gl_periods WHERE id=?',(period_id,)).fetchone()
    if not period: raise HTTPException(404,{'code':'PERIOD_NOT_FOUND'})
    rows,missing,failed=close_check_status(conn,period_id)
    blockers=[]
    if missing:blockers.append({'code':'MISSING_CLOSE_CHECKS','checks':missing})
    if failed:blockers.append({'code':'FAILED_CLOSE_CHECKS','checks':failed})
    tb=posted_trial_balance(conn)
    if not tb['balanced']:blockers.append({'code':'TRIAL_BALANCE_UNBALANCED','debit':tb['total_debit'],'credit':tb['total_credit']})
    unmatched=conn.execute("SELECT COUNT(*) n FROM gl_bank_statement_items WHERE matched=0").fetchone()['n']
    if unmatched:blockers.append({'code':'UNMATCHED_BANK_ITEMS','count':unmatched})
    return {'period_id':period_id,'period_status':period['status'],'checks':rows,'missing':missing,'failed':failed,'trial_balance':tb,'unmatched_bank_items':unmatched,'blockers':blockers,'ready':not blockers}

def assert_period_close_ready(conn,period_id):
    out=period_close_blockers(conn,period_id)
    if not out['ready']:
        raise HTTPException(422,{'code':'PERIOD_CLOSE_BLOCKED','blockers':out['blockers']})
    return out

def assert_period_transition(period,action,actor_user_ref,reason=None):
    state=str(period['status']).upper()
    action=str(action).lower()
    if action=='close':
        if state=='CLOSED':raise HTTPException(409,{'code':'PERIOD_ALREADY_CLOSED'})
        if state=='LOCKED':raise HTTPException(409,{'code':'LOCKED_PERIOD_CANNOT_CLOSE'})
    elif action=='lock':
        if state=='LOCKED':raise HTTPException(409,{'code':'PERIOD_ALREADY_LOCKED'})
        if state!='CLOSED':raise HTTPException(422,{'code':'PERIOD_MUST_BE_CLOSED_BEFORE_LOCK'})
    elif action=='open':
        if state=='OPEN':raise HTTPException(409,{'code':'PERIOD_ALREADY_OPEN'})
        if state not in {'CLOSED','LOCKED'}:raise HTTPException(422,{'code':'PERIOD_NOT_REOPENABLE','status':state})
        if not reason or len(str(reason).strip())<3:raise HTTPException(422,{'code':'REOPEN_REASON_REQUIRED'})
        prior=period['locked_by'] if state=='LOCKED' and 'locked_by' in period.keys() else period['closed_by'] if 'closed_by' in period.keys() else None
        if prior and prior==actor_user_ref:raise HTTPException(409,{'code':'FOUR_EYES_REOPEN_REQUIRED','prior_actor':prior})
    return True

def statement_snapshot(conn):
    tb=posted_trial_balance(conn)
    revenue=round(sum(-float(x['balance'] or 0) for x in tb['rows'] if x['account_type']=='REVENUE'),2)
    expenses=round(sum(float(x['balance'] or 0) for x in tb['rows'] if x['account_type']=='EXPENSE'),2)
    assets=round(sum(float(x['balance'] or 0) for x in tb['rows'] if x['account_type']=='ASSET'),2)
    liabilities=round(sum(-float(x['balance'] or 0) for x in tb['rows'] if x['account_type']=='LIABILITY'),2)
    return {
        'trial_balance':tb,
        'profit_loss':{'revenue':revenue,'expenses':expenses,'net_profit':round(revenue-expenses,2)},
        'balance_sheet':{'assets':assets,'liabilities':liabilities,'retained_result':round(assets-liabilities,2)}
    }

def assert_no_manual_report_override(fields):
    blocked={'Total Debit','Total Credit','Trial Balance Total','Revenue Total','Expense Total','Net Profit','Assets Total','Liabilities Total','Retained Result'}
    bad=sorted(k for k in fields if k in blocked)
    if bad:raise HTTPException(405,{'code':'FINANCIAL_STATEMENT_TOTALS_READ_ONLY','fields':bad})
    return True
