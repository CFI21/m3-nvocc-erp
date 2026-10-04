import os, json, datetime
from fastapi import HTTPException
from .bulk_permission_governance import authorize_job, resolve_job_scope

CASH_ACCOUNTS={'1000','1100'}

def enabled():
    return os.getenv('M3_ITEM12_REPORTING_CONSOLIDATION_GOVERNANCE_ENABLED','false').lower()=='true'

def _f(v):
    try:return float(v or 0)
    except:return 0.0

def _authorized_job_ids(conn,user_id):
    ids=set()
    for r in conn.execute("SELECT id,job_ref FROM jobs ORDER BY id"):
        if authorize_job(conn,user_id,r['job_ref'],'agent-tasks','view').get('allowed'):
            ids.add(r['id'])
    return ids

def posted_lines(conn,period=None,user_id=None,branch=None,office=None,country=None,organization=None,job_ref=None):
    sql="""SELECT v.id voucher_id,v.voucher_no,v.voucher_date,v.currency,v.exchange_rate,v.source_type,v.source_ref,
      l.line_no,l.account_code,l.debit,l.credit,l.job_id,
      a.account_name,a.account_type,
      j.job_ref,j.branch_code,j.office_code,j.country_code,j.organization_code
      FROM gl_vouchers v
      JOIN gl_voucher_lines l ON l.voucher_id=v.id
      LEFT JOIN gl_accounts a ON a.account_code=l.account_code
      LEFT JOIN jobs j ON j.id=l.job_id
      WHERE v.status='Posted'"""
    rows=[dict(r) for r in conn.execute(sql)]
    allowed=_authorized_job_ids(conn,user_id) if user_id is not None else None
    out=[]
    for r in rows:
        if period and not str(r['voucher_date'] or '').startswith(period):continue
        if allowed is not None and r['job_id'] is not None and r['job_id'] not in allowed:continue
        if r.get('job_ref'):
            scope=resolve_job_scope(conn,r['job_ref'],persist=False)
            r['branch_code']=scope.get('branch_code');r['office_code']=scope.get('office_code')
            r['country_code']=scope.get('country_code');r['organization_code']=scope.get('organization_code')
        if branch and r['branch_code']!=branch:continue
        if office and r['office_code']!=office:continue
        if country and r['country_code']!=country:continue
        if organization and r['organization_code']!=organization:continue
        if job_ref and r['job_ref']!=job_ref:continue
        rate=_f(r.get('exchange_rate')) or 1.0
        r['debit_base']=round(_f(r['debit'])*rate,2)
        r['credit_base']=round(_f(r['credit'])*rate,2)
        r['balance_base']=round(r['debit_base']-r['credit_base'],2)
        out.append(r)
    return out

def trial_balance(conn,**flt):
    agg={}
    for r in posted_lines(conn,**flt):
        a=agg.setdefault(r['account_code'],{'account_code':r['account_code'],'account_name':r['account_name'],'account_type':r['account_type'],'debit':0.0,'credit':0.0})
        a['debit']+=r['debit_base'];a['credit']+=r['credit_base']
    rows=[]
    for a in agg.values():
        a['debit']=round(a['debit'],2);a['credit']=round(a['credit'],2);a['balance']=round(a['debit']-a['credit'],2);rows.append(a)
    td=round(sum(x['debit'] for x in rows),2);tc=round(sum(x['credit'] for x in rows),2)
    return {'rows':sorted(rows,key=lambda x:x['account_code']),'total_debit':td,'total_credit':tc,'balanced':abs(td-tc)<0.01}

def profit_loss(conn,**flt):
    tb=trial_balance(conn,**flt)
    rev=round(sum(-x['balance'] for x in tb['rows'] if x['account_type']=='REVENUE'),2)
    exp=round(sum(x['balance'] for x in tb['rows'] if x['account_type']=='EXPENSE'),2)
    return {'revenue':rev,'expenses':exp,'net_profit':round(rev-exp,2)}

def balance_sheet(conn,**flt):
    tb=trial_balance(conn,**flt)
    assets=round(sum(x['balance'] for x in tb['rows'] if x['account_type']=='ASSET'),2)
    liabilities=round(sum(-x['balance'] for x in tb['rows'] if x['account_type']=='LIABILITY'),2)
    equity=round(sum(-x['balance'] for x in tb['rows'] if x['account_type'] in {'CAPITAL','EQUITY'}),2)
    return {'assets':assets,'liabilities':liabilities,'equity':equity,'balance_check':round(assets-liabilities-equity,2)}

def cash_flow(conn,**flt):
    rows=[r for r in posted_lines(conn,**flt) if r['account_code'] in CASH_ACCOUNTS]
    inflow=round(sum(r['debit_base'] for r in rows),2)
    outflow=round(sum(r['credit_base'] for r in rows),2)
    return {'inflow':inflow,'outflow':outflow,'net_cash_flow':round(inflow-outflow,2),'lines':rows}

def job_profitability(conn,**flt):
    agg={}
    for r in posted_lines(conn,**flt):
        if not r['job_ref']:continue
        a=agg.setdefault(r['job_ref'],{'job_ref':r['job_ref'],'branch_code':r['branch_code'],'office_code':r['office_code'],'country_code':r['country_code'],'organization_code':r['organization_code'],'revenue':0.0,'cost':0.0})
        if r['account_type']=='REVENUE':a['revenue']+=-r['balance_base']
        if r['account_type']=='EXPENSE':a['cost']+=r['balance_base']
    for a in agg.values():
        a['revenue']=round(a['revenue'],2);a['cost']=round(a['cost'],2);a['margin']=round(a['revenue']-a['cost'],2)
    return sorted(agg.values(),key=lambda x:x['job_ref'])

def scoped_pnl(conn,dimension,period=None,user_id=None):
    if dimension not in {'branch_code','office_code','country_code','organization_code'}:
        raise HTTPException(422,{'code':'INVALID_REPORT_DIMENSION'})
    agg={}
    for j in job_profitability(conn,period=period,user_id=user_id):
        key=j.get(dimension) or 'UNASSIGNED'
        a=agg.setdefault(key,{'scope':key,'revenue':0.0,'cost':0.0})
        a['revenue']+=j['revenue'];a['cost']+=j['cost']
    for a in agg.values():
        a['revenue']=round(a['revenue'],2);a['cost']=round(a['cost'],2);a['margin']=round(a['revenue']-a['cost'],2)
    return sorted(agg.values(),key=lambda x:x['scope'])

def validate_interbranch_eliminations(conn):
    rows=[dict(r) for r in conn.execute("""SELECT * FROM nvocc_interbranch_settlements
      WHERE elimination_flag=1 AND status='POSTED' ORDER BY settlement_ref""")]
    seen={}
    valid=[];invalid=[]
    for r in rows:
        ref=r.get('gl_posting_ref')
        v=conn.execute("SELECT id,voucher_no,status FROM gl_vouchers WHERE voucher_no=? OR CAST(id AS TEXT)=?",(ref,str(ref))).fetchone() if ref else None
        if not v or v['status']!='Posted':
            invalid.append({'settlement_ref':r['settlement_ref'],'code':'ELIMINATION_GL_POSTING_REQUIRED'});continue
        if ref in seen:
            invalid.append({'settlement_ref':r['settlement_ref'],'code':'DUPLICATE_CONSOLIDATION_GL_REF','first':seen[ref]});continue
        seen[ref]=r['settlement_ref'];valid.append(r)
    return {'valid':valid,'invalid':invalid}

def consolidated_reporting(conn,period=None,user_id=None):
    org=scoped_pnl(conn,'organization_code',period,user_id)
    elim=validate_interbranch_eliminations(conn)
    if elim['invalid']:
        raise HTTPException(422,{'code':'CONSOLIDATION_ELIMINATION_INVALID','exceptions':elim['invalid']})
    elimination_total=round(sum(_f(r.get('total_amount'))*(_f(r.get('exchange_rate')) or 1.0) for r in elim['valid']),2)
    revenue=round(sum(x['revenue'] for x in org),2)
    cost=round(sum(x['cost'] for x in org),2)
    return {'organizations':org,'gross_margin':round(revenue-cost,2),'elimination_total':elimination_total,'consolidated_margin':round(revenue-cost-elimination_total,2),'source':'POSTED_GL_PLUS_GOVERNED_INTERBRANCH_ELIMINATION'}

def assert_no_report_override(fields):
    blocked={'Total','Revenue','Cost','Margin','Net Profit','Assets','Liabilities','Equity','Cash Flow','Consolidated Margin','Elimination Total'}
    bad=sorted(k for k in fields if k in blocked)
    if bad:raise HTTPException(405,{'code':'MANAGEMENT_REPORT_TOTALS_READ_ONLY','fields':bad})
    return True

def request_elimination(conn,settlement_ref,user_ref,reason):
    if not reason or len(str(reason).strip())<3:raise HTTPException(422,{'code':'ELIMINATION_REASON_REQUIRED'})
    r=conn.execute("SELECT * FROM nvocc_interbranch_settlements WHERE settlement_ref=?",(settlement_ref,)).fetchone()
    if not r:raise HTTPException(404,{'code':'INTERBRANCH_SETTLEMENT_NOT_FOUND'})
    if r['status']!='POSTED':raise HTTPException(422,{'code':'POSTED_SETTLEMENT_REQUIRED_FOR_ELIMINATION'})
    if r['elimination_status'] in {'PENDING','APPROVED'}:raise HTTPException(409,{'code':'ELIMINATION_ALREADY_REQUESTED'})
    conn.execute("""UPDATE nvocc_interbranch_settlements SET elimination_flag=0,elimination_status='PENDING',
      elimination_requested_by=?,elimination_reason=?,elimination_requested_at=?,version=version+1 WHERE settlement_ref=?""",
      (user_ref,reason,datetime.datetime.now(datetime.timezone.utc).isoformat(),settlement_ref))
    return dict(conn.execute("SELECT * FROM nvocc_interbranch_settlements WHERE settlement_ref=?",(settlement_ref,)).fetchone())

def approve_elimination(conn,settlement_ref,user_ref):
    r=conn.execute("SELECT * FROM nvocc_interbranch_settlements WHERE settlement_ref=?",(settlement_ref,)).fetchone()
    if not r:raise HTTPException(404,{'code':'INTERBRANCH_SETTLEMENT_NOT_FOUND'})
    if r['elimination_status']!='PENDING':raise HTTPException(409,{'code':'ELIMINATION_NOT_PENDING'})
    if r['elimination_requested_by']==user_ref:raise HTTPException(409,{'code':'FOUR_EYES_ELIMINATION_REQUIRED'})
    if not r['gl_posting_ref']:raise HTTPException(422,{'code':'ELIMINATION_GL_POSTING_REQUIRED'})
    v=conn.execute("SELECT id,status FROM gl_vouchers WHERE voucher_no=? OR CAST(id AS TEXT)=?",(r['gl_posting_ref'],str(r['gl_posting_ref']))).fetchone()
    if not v or v['status']!='Posted':raise HTTPException(422,{'code':'ELIMINATION_GL_POSTING_REQUIRED'})
    dup=conn.execute("""SELECT settlement_ref FROM nvocc_interbranch_settlements WHERE settlement_ref<>?
      AND elimination_flag=1 AND gl_posting_ref=? LIMIT 1""",(settlement_ref,r['gl_posting_ref'])).fetchone()
    if dup:raise HTTPException(409,{'code':'DUPLICATE_CONSOLIDATION_GL_REF','existing':dup['settlement_ref']})
    conn.execute("""UPDATE nvocc_interbranch_settlements SET elimination_flag=1,elimination_status='APPROVED',
      elimination_approved_by=?,elimination_approved_at=?,version=version+1 WHERE settlement_ref=?""",
      (user_ref,datetime.datetime.now(datetime.timezone.utc).isoformat(),settlement_ref))
    return dict(conn.execute("SELECT * FROM nvocc_interbranch_settlements WHERE settlement_ref=?",(settlement_ref,)).fetchone())
