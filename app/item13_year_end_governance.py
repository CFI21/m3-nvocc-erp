import os, json, datetime
from fastapi import HTTPException
from .item11_period_reporting_governance import posted_trial_balance, close_check_status
from .item12_reporting_consolidation_governance import validate_interbranch_eliminations

def enabled():
    return os.getenv('M3_ITEM13_YEAR_END_GOVERNANCE_ENABLED','false').lower()=='true'

def year(conn,fiscal_year):
    r=conn.execute("SELECT * FROM gl_fiscal_years WHERE fiscal_year=?",(fiscal_year,)).fetchone()
    if not r: raise HTTPException(404,{'code':'FISCAL_YEAR_NOT_FOUND','fiscal_year':fiscal_year})
    return r

def year_periods(conn,year_id):
    return [dict(r) for r in conn.execute("SELECT * FROM gl_periods WHERE fiscal_year_id=? ORDER BY period_no",(year_id,))]

def year_end_readiness(conn,fiscal_year):
    fy=year(conn,fiscal_year); periods=year_periods(conn,fy['id'])
    blockers=[]
    if len(periods)!=12:
        blockers.append({'code':'FISCAL_YEAR_PERIOD_SET_INCOMPLETE','count':len(periods)})
    open_periods=[p['period_no'] for p in periods if str(p['status']).upper() not in {'CLOSED','LOCKED'}]
    if open_periods: blockers.append({'code':'PERIODS_NOT_CLOSED','periods':open_periods})
    p12=next((p for p in periods if p['period_no']==12),None)
    if p12:
        _,missing,failed=close_check_status(conn,p12['id'])
        if missing:blockers.append({'code':'YEAR_END_CLOSE_CHECKS_MISSING','checks':missing})
        if failed:blockers.append({'code':'YEAR_END_CLOSE_CHECKS_FAILED','checks':failed})
        if not p12.get('tax_filed_at'):
            blockers.append({'code':'YEAR_END_TAX_FILED_EVIDENCE_REQUIRED','period_id':p12['id']})
    tb=posted_trial_balance(conn)
    if not tb['balanced']:blockers.append({'code':'YEAR_END_TRIAL_BALANCE_UNBALANCED','debit':tb['total_debit'],'credit':tb['total_credit']})
    unmatched=conn.execute("SELECT COUNT(*) n FROM gl_bank_statement_items WHERE matched=0 AND txn_date BETWEEN ? AND ?",(fy['start_date'],fy['end_date'])).fetchone()['n']
    if unmatched:blockers.append({'code':'YEAR_END_BANK_UNMATCHED','count':unmatched})
    accr=[dict(r) for r in conn.execute("""SELECT * FROM gl_accrual_schedules a JOIN gl_periods p ON p.id=a.period_id
      WHERE p.fiscal_year_id=? AND date(a.reverse_date)<=date(?) AND UPPER(a.status) NOT IN ('REVERSED','CLOSED')""",(fy['id'],f'{fiscal_year+1}-01-31'))]
    if accr:blockers.append({'code':'YEAR_END_ACCRUAL_REVERSALS_PENDING','count':len(accr)})
    foreign=[r['currency'] for r in conn.execute("""SELECT DISTINCT currency FROM gl_vouchers
      WHERE status='Posted' AND date(voucher_date) BETWEEN date(?) AND date(?) AND currency<>'USD'""",(fy['start_date'],fy['end_date']))]
    if foreign and p12:
        have={r['currency'] for r in conn.execute("SELECT DISTINCT currency FROM gl_fx_events WHERE period_id=? AND event_type='UNREALIZED' AND status NOT IN ('REVERSED','CANCELLED')",(p12['id'],))}
        missing_fx=sorted(set(foreign)-have)
        if missing_fx:blockers.append({'code':'YEAR_END_FX_REVALUATION_MISSING','currencies':missing_fx})
    elim=validate_interbranch_eliminations(conn)
    if elim['invalid']:blockers.append({'code':'YEAR_END_INTERCOMPANY_ELIMINATION_INVALID','exceptions':elim['invalid']})
    ar_check=next((r for r in conn.execute("SELECT * FROM gl_period_close_checks WHERE period_id=? AND check_code='AR_AP'",(p12['id'],))) if p12 else [],None)
    if p12 and (not ar_check or str(ar_check['status']).upper()!='PASS'):
        blockers.append({'code':'YEAR_END_AR_AP_CONFIRMATION_REQUIRED'})
    return {'fiscal_year':fiscal_year,'fiscal_year_id':fy['id'],'status':fy['status'],'periods':periods,'trial_balance':tb,
            'unmatched_bank_items':unmatched,'intercompany':elim,'blockers':blockers,'ready':not blockers}

def assert_year_end_ready(conn,fiscal_year):
    out=year_end_readiness(conn,fiscal_year)
    if not out['ready']:raise HTTPException(422,{'code':'YEAR_END_CLOSE_BLOCKED','blockers':out['blockers']})
    return out

def retained_earnings_amount(conn,fiscal_year):
    fy=year(conn,fiscal_year)
    rows=conn.execute("""SELECT a.account_type,
      SUM((l.credit-l.debit)*COALESCE(v.exchange_rate,1)) amount
      FROM gl_vouchers v JOIN gl_voucher_lines l ON l.voucher_id=v.id
      JOIN gl_accounts a ON a.account_code=l.account_code
      WHERE v.status='Posted' AND date(v.voucher_date) BETWEEN date(?) AND date(?)
      AND a.account_type IN ('REVENUE','EXPENSE')
      GROUP BY a.account_type""",(fy['start_date'],fy['end_date'])).fetchall()
    revenue=round(sum(float(r['amount'] or 0) for r in rows if r['account_type']=='REVENUE'),2)
    expense_credit_minus_debit=round(sum(float(r['amount'] or 0) for r in rows if r['account_type']=='EXPENSE'),2)
    expenses=round(-expense_credit_minus_debit,2)
    return {'revenue':revenue,'expenses':expenses,'net_profit':round(revenue-expenses,2)}

def carry_forward_rows(conn,fiscal_year):
    fy=year(conn,fiscal_year)
    rows=conn.execute("""SELECT a.account_code,a.account_name,a.account_type,
      ROUND(SUM((l.debit-l.credit)*COALESCE(v.exchange_rate,1)),2) balance
      FROM gl_vouchers v JOIN gl_voucher_lines l ON l.voucher_id=v.id
      JOIN gl_accounts a ON a.account_code=l.account_code
      WHERE v.status='Posted' AND date(v.voucher_date)<=date(?)
      AND a.account_type IN ('ASSET','LIABILITY','CAPITAL','EQUITY')
      GROUP BY a.account_code,a.account_name,a.account_type ORDER BY a.account_code""",(fy['end_date'],)).fetchall()
    return [dict(r) for r in rows if abs(float(r['balance'] or 0))>=0.005]

def generate_opening_balances(conn,fiscal_year,actor_user_ref):
    fy=year(conn,fiscal_year); next_year=fiscal_year+1
    existing=conn.execute("SELECT COUNT(*) n FROM gl_records WHERE module='opening-balance' AND external_ref LIKE ?",(f'YECF-{next_year}-%',)).fetchone()['n']
    if existing:raise HTTPException(409,{'code':'DUPLICATE_YEAR_END_CARRY_FORWARD','existing':existing})
    rows=carry_forward_rows(conn,fiscal_year)
    re=retained_earnings_amount(conn,fiscal_year)
    retained=fy['retained_earnings_account'] or '3000'
    by={r['account_code']:dict(r) for r in rows}
    rr=by.setdefault(retained,{'account_code':retained,'account_name':'Retained / Opening Equity','account_type':'CAPITAL','balance':0.0})
    rr['balance']=round(float(rr['balance'] or 0)-re['net_profit'],2)
    created=[]
    ts=datetime.datetime.now(datetime.timezone.utc).isoformat()
    for code,r in sorted(by.items()):
        bal=round(float(r['balance'] or 0),2)
        ext=f'YECF-{next_year}-{code}'
        debit=bal if bal>0 else 0.0; credit=-bal if bal<0 else 0.0
        payload={'Period':f'{next_year}-01','Account Code':code,'Account Name':r.get('account_name'),'Debit':str(round(debit,2)),
                 'Credit':str(round(credit,2)),'Currency':'USD','Status':'Posted','Source Fiscal Year':str(fiscal_year),
                 'Generated By':actor_user_ref,'Carry Forward':'Yes'}
        conn.execute("""INSERT INTO gl_records(module,external_ref,job_id,source_type,source_ref,status,version,payload_json,created_at,updated_at)
          VALUES('opening-balance',?,NULL,'YEAR_END_CARRY_FORWARD',?,'Posted',1,?,?,?)""",(ext,f'FY-{fiscal_year}',json.dumps(payload),ts,ts))
        created.append(ext)
    ref=f'YECF-{fiscal_year}-{next_year}'
    conn.execute("UPDATE gl_fiscal_years SET carry_forward_ref=? WHERE id=?",(ref,fy['id']))
    return {'carry_forward_ref':ref,'opening_balances':created,'retained_earnings':re,'retained_earnings_account':retained}

def assert_year_transition(fy,action,actor_user_ref,reason=None):
    state=str(fy['status']).upper();action=action.lower()
    if action=='close':
        if state=='CLOSED':raise HTTPException(409,{'code':'FISCAL_YEAR_ALREADY_CLOSED'})
        if state=='LOCKED':raise HTTPException(409,{'code':'LOCKED_FISCAL_YEAR_CANNOT_CLOSE'})
    elif action=='lock':
        if state=='LOCKED':raise HTTPException(409,{'code':'FISCAL_YEAR_ALREADY_LOCKED'})
        if state!='CLOSED':raise HTTPException(422,{'code':'FISCAL_YEAR_MUST_BE_CLOSED_BEFORE_LOCK'})
    elif action=='open':
        if state=='OPEN':raise HTTPException(409,{'code':'FISCAL_YEAR_ALREADY_OPEN'})
        if not reason or len(str(reason).strip())<3:raise HTTPException(422,{'code':'FISCAL_YEAR_REOPEN_REASON_REQUIRED'})
        prior=(fy['locked_by'] if state=='LOCKED' and 'locked_by' in fy.keys() else None) or (fy['closed_by'] if 'closed_by' in fy.keys() else None)
        if prior and prior==actor_user_ref:raise HTTPException(409,{'code':'FOUR_EYES_FISCAL_YEAR_REOPEN_REQUIRED','prior_actor':prior})
    return True

def statutory_audit_snapshot(conn,fiscal_year):
    fy=year(conn,fiscal_year)
    vouchers=[dict(r) for r in conn.execute("""SELECT voucher_no,voucher_date,status,source_type,source_ref,job_id,posted_at
      FROM gl_vouchers WHERE status='Posted' AND date(voucher_date) BETWEEN date(?) AND date(?) ORDER BY voucher_date,voucher_no""",(fy['start_date'],fy['end_date']))]
    links=[dict(r) for r in conn.execute("""SELECT l.source_type,l.source_ref,j.job_ref,v.voucher_no
      FROM gl_source_links l JOIN gl_vouchers v ON v.id=l.voucher_id
      LEFT JOIN jobs j ON j.id=l.job_id
      WHERE v.status='Posted' AND date(v.voucher_date) BETWEEN date(?) AND date(?) ORDER BY l.id""",(fy['start_date'],fy['end_date']))]
    return {'fiscal_year':fiscal_year,'status':fy['status'],'vouchers':vouchers,'source_links':links,
            'retained_earnings':retained_earnings_amount(conn,fiscal_year),'carry_forward_ref':fy['carry_forward_ref'],'read_only':True}
