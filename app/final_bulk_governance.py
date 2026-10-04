import os, datetime, uuid
from fastapi import HTTPException
from .bulk_permission_governance import authorize_job, resolve_job_scope
from .item12_reporting_consolidation_governance import posted_lines, job_profitability, scoped_pnl, cash_flow

def enabled():
    return os.getenv('M3_FINAL_BULK_COMPLETION_ENABLED','false').lower()=='true'

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def _f(v):
    try:return float(v)
    except:return 0.0

def _period_key(fy,period):
    return f"{int(fy):04d}-{int(period):02d}"

def _account(conn,code):
    r=conn.execute("SELECT * FROM gl_accounts WHERE account_code=?",(str(code),)).fetchone()
    if not r: raise HTTPException(422,{'code':'BUDGET_ACCOUNT_INVALID','account_code':code})
    return r

def _scope_values(conn,user_id):
    vals={'job_ref':set(),'branch_code':set(),'office_code':set(),'country_code':set(),'organization_code':set()}
    jobs=[dict(r) for r in conn.execute("SELECT job_ref FROM jobs ORDER BY id")]
    for j in jobs:
        a=authorize_job(conn,user_id,j['job_ref'],'gl','view')
        if not a.get('allowed'):continue
        s=resolve_job_scope(conn,j['job_ref'],persist=False)
        vals['job_ref'].add(j['job_ref'])
        for k in ('branch_code','office_code','country_code','organization_code'):
            if s.get(k):vals[k].add(s[k])
    vals['all_jobs']=len(vals['job_ref'])==len(jobs)
    return vals

def assert_scope(conn,user_id,job_ref=None,branch_code=None,office_code=None,country_code=None,organization_code=None):
    vals=_scope_values(conn,user_id)
    requested={
      'job_ref':job_ref,'branch_code':branch_code,'office_code':office_code,
      'country_code':country_code,'organization_code':organization_code
    }
    if not any(requested.values()):
        if not vals['all_jobs']:raise HTTPException(403,{'code':'GLOBAL_BUDGET_SCOPE_REQUIRED'})
        return vals
    for k,v in requested.items():
        if v and v not in vals[k]:
            raise HTTPException(403,{'code':'BUDGET_SCOPE_DENIED','scope_type':k,'scope_value':v})
    if job_ref:
        s=resolve_job_scope(conn,job_ref,persist=False)
        for k in ('branch_code','office_code','country_code','organization_code'):
            v=requested.get(k)
            if v and s.get(k)!=v:
                raise HTTPException(422,{'code':'BUDGET_SCOPE_ATTRIBUTION_MISMATCH','job_ref':job_ref,'scope_type':k,'expected':s.get(k),'supplied':v})
    return vals

def actual_amount(conn,line,user_id=None):
    period=_period_key(line['fiscal_year'],line['period'])
    rows=posted_lines(
        conn,period=period,user_id=user_id,
        branch=line['branch_code'],office=line['office_code'],
        country=line['country_code'],organization=line['organization_code'],
        job_ref=line['job_ref']
    )
    rows=[r for r in rows if r['account_code']==line['account_code']]
    acct=_account(conn,line['account_code'])
    bal=round(sum(float(r['balance_base']) for r in rows),2)
    if acct['account_type'] in {'REVENUE','LIABILITY','CAPITAL','EQUITY'}: bal=-bal
    return round(bal,2)

def serialize_line(conn,row,user_id=None):
    d=dict(row)
    act=actual_amount(conn,d,user_id)
    bud=round(float(d['amount']),2)
    var=round(act-bud,2)
    pct=round((var/bud*100),2) if abs(bud)>0.000001 else None
    d.update({'actual_amount':act,'variance_amount':var,'variance_pct':pct,'actual_source':'POSTED_GL','actual_read_only':True})
    return d

def validate_input(conn,data,user_id):
    fy=int(data.get('fiscal_year')); period=int(data.get('period')); code=str(data.get('account_code') or '')
    if period<1 or period>12:raise HTTPException(422,{'code':'BUDGET_PERIOD_INVALID'})
    _account(conn,code)
    amt=_f(data.get('amount'))
    if amt<0:raise HTTPException(422,{'code':'BUDGET_AMOUNT_INVALID'})
    scenario=str(data.get('scenario') or 'BUDGET').upper()
    if scenario not in {'BUDGET','FORECAST'}:raise HTTPException(422,{'code':'BUDGET_SCENARIO_INVALID'})
    for forbidden in ('actual_amount','variance','variance_amount','variance_pct','Actual Amount','Variance'):
        if forbidden in data:raise HTTPException(405,{'code':'ACTUAL_VARIANCE_READ_ONLY','field':forbidden})
    assert_scope(conn,user_id,data.get('job_ref'),data.get('branch_code'),data.get('office_code'),data.get('country_code'),data.get('organization_code'))
    for pc in ('cost_center','profit_center'):
        val=data.get(pc)
        if val:
            found=conn.execute(f"SELECT 1 FROM nvocc_branch_profiles WHERE {pc}=? AND active=1 LIMIT 1",(val,)).fetchone()
            if not found:raise HTTPException(422,{'code':'BUDGET_CENTER_INVALID','field':pc,'value':val})
    return {'fiscal_year':fy,'period':period,'account_code':code,'amount':amt,'scenario':scenario}

def create_line(conn,data,user_id,user_ref):
    core=validate_input(conn,data,user_id)
    scope=[data.get(k) for k in ('job_ref','branch_code','office_code','country_code','organization_code','cost_center','profit_center')]
    dup=conn.execute("""SELECT budget_ref FROM gl_budget_lines WHERE fiscal_year=? AND period=? AND account_code=? AND scenario=?
      AND COALESCE(job_ref,'')=COALESCE(?,'') AND COALESCE(branch_code,'')=COALESCE(?,'')
      AND COALESCE(office_code,'')=COALESCE(?,'') AND COALESCE(country_code,'')=COALESCE(?,'')
      AND COALESCE(organization_code,'')=COALESCE(?,'') AND COALESCE(cost_center,'')=COALESCE(?,'')
      AND COALESCE(profit_center,'')=COALESCE(?,'') AND status IN ('DRAFT','SUBMITTED','APPROVED') LIMIT 1""",
      (core['fiscal_year'],core['period'],core['account_code'],core['scenario'],*scope)).fetchone()
    if dup:raise HTTPException(409,{'code':'DUPLICATE_BUDGET_LINE','existing_ref':dup['budget_ref']})
    ref=data.get('budget_ref') or 'BUD-'+uuid.uuid4().hex[:10].upper()
    conn.execute("""INSERT INTO gl_budget_lines(budget_ref,fiscal_year,period,account_code,amount,scenario,job_ref,branch_code,office_code,country_code,organization_code,cost_center,profit_center,status,requested_by,requested_at,revision_no,version)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'DRAFT',?,?,1,1)""",
      (ref,core['fiscal_year'],core['period'],core['account_code'],core['amount'],core['scenario'],*scope,user_ref,now()))
    return serialize_line(conn,conn.execute("SELECT * FROM gl_budget_lines WHERE budget_ref=?",(ref,)).fetchone(),user_id)

def submit_line(conn,ref,user_id,user_ref,version):
    r=conn.execute("SELECT * FROM gl_budget_lines WHERE budget_ref=?",(ref,)).fetchone()
    if not r:raise HTTPException(404,{'code':'BUDGET_LINE_NOT_FOUND'})
    assert_scope(conn,user_id,r['job_ref'],r['branch_code'],r['office_code'],r['country_code'],r['organization_code'])
    if r['version']!=version:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
    if r['status']!='DRAFT':raise HTTPException(409,{'code':'BUDGET_NOT_DRAFT','status':r['status']})
    conn.execute("UPDATE gl_budget_lines SET status='SUBMITTED',requested_by=?,requested_at=?,version=version+1 WHERE id=?",(user_ref,now(),r['id']))
    return serialize_line(conn,conn.execute("SELECT * FROM gl_budget_lines WHERE id=?",(r['id'],)).fetchone(),user_id)

def approve_line(conn,ref,user_id,user_ref,version):
    r=conn.execute("SELECT * FROM gl_budget_lines WHERE budget_ref=?",(ref,)).fetchone()
    if not r:raise HTTPException(404,{'code':'BUDGET_LINE_NOT_FOUND'})
    assert_scope(conn,user_id,r['job_ref'],r['branch_code'],r['office_code'],r['country_code'],r['organization_code'])
    if r['version']!=version:raise HTTPException(409,{'code':'OPTIMISTIC_LOCK_CONFLICT','current_version':r['version']})
    if r['status']!='SUBMITTED':raise HTTPException(409,{'code':'BUDGET_NOT_SUBMITTED','status':r['status']})
    if r['requested_by']==user_ref:raise HTTPException(409,{'code':'BUDGET_SELF_APPROVAL_BLOCKED'})
    conn.execute("UPDATE gl_budget_lines SET status='APPROVED',approved_by=?,approved_at=?,version=version+1 WHERE id=?",(user_ref,now(),r['id']))
    if r['parent_budget_ref']:
        conn.execute("UPDATE gl_budget_lines SET status='REVISED',version=version+1 WHERE budget_ref=? AND status='APPROVED'",(r['parent_budget_ref'],))
    return serialize_line(conn,conn.execute("SELECT * FROM gl_budget_lines WHERE id=?",(r['id'],)).fetchone(),user_id)

def revise_line(conn,ref,data,user_id,user_ref):
    r=conn.execute("SELECT * FROM gl_budget_lines WHERE budget_ref=?",(ref,)).fetchone()
    if not r:raise HTTPException(404,{'code':'BUDGET_LINE_NOT_FOUND'})
    if r['status']!='APPROVED':raise HTTPException(409,{'code':'ONLY_APPROVED_BUDGET_CAN_REVISE'})
    reason=str(data.get('reason') or '').strip()
    if len(reason)<3:raise HTTPException(422,{'code':'BUDGET_REVISION_REASON_REQUIRED'})
    pending=conn.execute("SELECT budget_ref FROM gl_budget_lines WHERE parent_budget_ref=? AND status IN ('DRAFT','SUBMITTED') LIMIT 1",(ref,)).fetchone()
    if pending:raise HTTPException(409,{'code':'DUPLICATE_BUDGET_REVISION','existing_ref':pending['budget_ref']})
    newdata={k:r[k] for k in ('fiscal_year','period','account_code','scenario','job_ref','branch_code','office_code','country_code','organization_code','cost_center','profit_center')}
    newdata['amount']=data.get('amount',r['amount'])
    newdata['budget_ref']=data.get('budget_ref') or 'BUDR-'+uuid.uuid4().hex[:10].upper()
    core=validate_input(conn,newdata,user_id)
    conn.execute("""INSERT INTO gl_budget_lines(budget_ref,fiscal_year,period,account_code,amount,scenario,job_ref,branch_code,office_code,country_code,organization_code,cost_center,profit_center,status,requested_by,requested_at,revision_no,parent_budget_ref,revision_reason,version)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'DRAFT',?,?,?,?,?,1)""",
      (newdata['budget_ref'],core['fiscal_year'],core['period'],core['account_code'],core['amount'],core['scenario'],newdata['job_ref'],newdata['branch_code'],newdata['office_code'],newdata['country_code'],newdata['organization_code'],newdata['cost_center'],newdata['profit_center'],user_ref,now(),int(r['revision_no'])+1,ref,reason))
    return serialize_line(conn,conn.execute("SELECT * FROM gl_budget_lines WHERE budget_ref=?",(newdata['budget_ref'],)).fetchone(),user_id)

def management_rollup(conn,user_id,fiscal_year=None,period=None):
    sql="SELECT * FROM gl_budget_lines WHERE status='APPROVED'"
    args=[]
    if fiscal_year is not None:sql+=" AND fiscal_year=?";args.append(int(fiscal_year))
    if period is not None:sql+=" AND period=?";args.append(int(period))
    lines=[]
    for r in conn.execute(sql,args):
        try: assert_scope(conn,user_id,r['job_ref'],r['branch_code'],r['office_code'],r['country_code'],r['organization_code'])
        except HTTPException: continue
        lines.append(serialize_line(conn,r,user_id))
    totals={'budget':round(sum(float(x['amount']) for x in lines),2),'actual':round(sum(float(x['actual_amount']) for x in lines),2)}
    totals['variance']=round(totals['actual']-totals['budget'],2)
    totals['variance_pct']=round(totals['variance']/totals['budget']*100,2) if totals['budget'] else None
    return {
      'lines':lines,'totals':totals,
      'job_profitability':job_profitability(conn,period=_period_key(fiscal_year,period) if fiscal_year and period else None,user_id=user_id),
      'branch_pnl':scoped_pnl(conn,'branch_code',period=_period_key(fiscal_year,period) if fiscal_year and period else None,user_id=user_id),
      'office_pnl':scoped_pnl(conn,'office_code',period=_period_key(fiscal_year,period) if fiscal_year and period else None,user_id=user_id),
      'country_pnl':scoped_pnl(conn,'country_code',period=_period_key(fiscal_year,period) if fiscal_year and period else None,user_id=user_id),
      'organization_pnl':scoped_pnl(conn,'organization_code',period=_period_key(fiscal_year,period) if fiscal_year and period else None,user_id=user_id),
      'cash':cash_flow(conn,period=_period_key(fiscal_year,period) if fiscal_year and period else None,user_id=user_id),
      'actual_source':'POSTED_GL','read_only_actuals':True
    }

def assert_no_management_override(fields):
    blocked={'Actual Amount','Variance','Variance %','Revenue','Cost','Margin','Cash','AR','AP','Credit','Exceptions','KPI'}
    bad=sorted(k for k in fields if k in blocked)
    if bad:raise HTTPException(405,{'code':'MANAGEMENT_ACTUAL_KPI_READ_ONLY','fields':bad})
    return True
