from fastapi import APIRouter,Header,HTTPException,Query
from typing import Optional
import json
from .db import connect
from .gl import actor

router=APIRouter(prefix='/api/clx045',tags=['CLX-045 GL Reporting + Financial Control'])

REPORT_KEYS={'trial-balance-report','profit-loss','balance-sheet','gl-detail','subledger-gl-reconciliation'}

def _payload(raw):
    if not raw:return {}
    if isinstance(raw,dict):return raw
    try:return json.loads(raw)
    except Exception:return {}

def _f(v):
    try:return float(v or 0)
    except Exception:return 0.0

def _match_filters(v,payload,company,period,date_from,date_to,currency,status,cost_center):
    if status and str(v.get('status') or '').upper()!=status.upper():return False
    if currency and str(v.get('currency') or '').upper()!=currency.upper():return False
    d=str(v.get('voucher_date') or '')
    if period and not d.startswith(period):return False
    if date_from and d<date_from:return False
    if date_to and d>date_to:return False
    if company and str(payload.get('Company') or payload.get('company') or '').lower()!=company.lower():return False
    if cost_center and str(payload.get('Cost Center') or payload.get('Cost Centre') or '').lower()!=cost_center.lower():return False
    return True

def _posted_lines(company=None,period=None,date_from=None,date_to=None,account=None,currency=None,status='Posted',cost_center=None):
    c=connect()
    try:
        rows=[]
        sql='''SELECT v.id voucher_id,v.gl_record_id,v.voucher_no,v.voucher_type,v.voucher_date,v.currency,v.status,
          v.source_type,v.source_ref,v.job_id,v.exchange_rate,v.base_currency,
          l.id line_id,l.line_no,l.account_code,l.debit,l.credit,l.description,
          a.account_name,a.account_type,g.external_ref,g.payload_json
          FROM gl_vouchers v
          JOIN gl_voucher_lines l ON l.voucher_id=v.id
          LEFT JOIN gl_accounts a ON a.account_code=l.account_code
          LEFT JOIN gl_records g ON g.id=v.gl_record_id
          ORDER BY v.voucher_date,v.voucher_no,l.line_no'''
        for r in c.execute(sql):
            d=dict(r);p=_payload(d.pop('payload_json',None))
            if account and d['account_code']!=account:continue
            if not _match_filters(d,p,company,period,date_from,date_to,currency,status,cost_center):continue
            rate=_f(d.get('exchange_rate')) or 1.0
            d['company']=p.get('Company') or p.get('company')
            d['cost_center']=p.get('Cost Center') or p.get('Cost Centre')
            d['debit_vc']=round(_f(d['debit']),2);d['credit_vc']=round(_f(d['credit']),2)
            d['debit_lc']=round(_f(d['debit'])*rate,2);d['credit_lc']=round(_f(d['credit'])*rate,2)
            d['balance_vc']=round(d['debit_vc']-d['credit_vc'],2)
            d['balance_lc']=round(d['debit_lc']-d['credit_lc'],2)
            rows.append(d)
        return rows
    finally:c.close()

def _opening_balances(company=None,period=None,account=None,currency=None,cost_center=None):
    c=connect()
    try:
        out=[]
        for r in c.execute("SELECT id,external_ref,status,payload_json FROM gl_records WHERE module='opening-balance' ORDER BY id"):
            d=dict(r);p=_payload(d['payload_json'])
            if str(d['status']).upper() not in {'APPROVED','POSTED','ACTIVE'}:continue
            if account and str(p.get('Account Code'))!=account:continue
            if period and str(p.get('Period') or '')>period:continue
            if currency and str(p.get('Currency') or '').upper()!=currency.upper():continue
            if company and str(p.get('Company') or '').lower()!=company.lower():continue
            if cost_center and str(p.get('Cost Center') or p.get('Cost Centre') or '').lower()!=cost_center.lower():continue
            out.append({
              'account_code':str(p.get('Account Code') or ''),
              'account_name':p.get('Account Name'),
              'period':p.get('Period'),'currency':p.get('Currency'),
              'debit_vc':round(_f(p.get('Debit')),2),'credit_vc':round(_f(p.get('Credit')),2),
              'debit_lc':round(_f(p.get('Debit')),2),'credit_lc':round(_f(p.get('Credit')),2),
              'source_ref':d['external_ref'],'source_type':'OPENING_BALANCE'
            })
        return out
    finally:c.close()

def trial_balance_rows(**flt):
    c=connect()
    try: accounts={r['account_code']:dict(r) for r in c.execute('SELECT * FROM gl_accounts ORDER BY account_code')}
    finally:c.close()
    sums={}
    for code,a in accounts.items():
        sums[code]={'Account Code':code,'Account Name':a['account_name'],'Account Type':a['account_type'],'Opening Debit':0.0,'Opening Credit':0.0,'Movement Debit VC':0.0,'Movement Credit VC':0.0,'Debit LC':0.0,'Credit LC':0.0}
    for x in _opening_balances(company=flt.get('company'),period=flt.get('period'),account=flt.get('account'),currency=flt.get('currency'),cost_center=flt.get('cost_center')):
        r=sums.setdefault(x['account_code'],{'Account Code':x['account_code'],'Account Name':x.get('account_name'),'Account Type':'','Opening Debit':0.0,'Opening Credit':0.0,'Movement Debit VC':0.0,'Movement Credit VC':0.0,'Debit LC':0.0,'Credit LC':0.0})
        r['Opening Debit']+=x['debit_vc'];r['Opening Credit']+=x['credit_vc'];r['Debit LC']+=x['debit_lc'];r['Credit LC']+=x['credit_lc']
    for x in _posted_lines(**flt):
        r=sums[x['account_code']]
        r['Movement Debit VC']+=x['debit_vc'];r['Movement Credit VC']+=x['credit_vc'];r['Debit LC']+=x['debit_lc'];r['Credit LC']+=x['credit_lc']
    rows=[]
    for r in sums.values():
        r={k:(round(v,2) if isinstance(v,float) else v) for k,v in r.items()}
        r['Debit VC']=round(r['Opening Debit']+r['Movement Debit VC'],2)
        r['Credit VC']=round(r['Opening Credit']+r['Movement Credit VC'],2)
        r['Balance VC']=round(r['Debit VC']-r['Credit VC'],2)
        r['Balance LC']=round(r['Debit LC']-r['Credit LC'],2)
        if flt.get('account') and r['Account Code']!=flt['account']:continue
        rows.append(r)
    return rows

def _totals(rows):
    return {
      'debit_vc':round(sum(_f(r.get('Debit VC')) for r in rows),2),
      'credit_vc':round(sum(_f(r.get('Credit VC')) for r in rows),2),
      'debit_lc':round(sum(_f(r.get('Debit LC')) for r in rows),2),
      'credit_lc':round(sum(_f(r.get('Credit LC')) for r in rows),2)
    }

def profit_loss_rows(**flt):
    rows=[]
    for r in trial_balance_rows(**flt):
        typ=str(r['Account Type'] or '').upper()
        if typ not in {'REVENUE','EXPENSE'}:continue
        amount=round(r['Credit LC']-r['Debit LC'],2) if typ=='REVENUE' else round(r['Debit LC']-r['Credit LC'],2)
        rows.append({'Section':'Revenue' if typ=='REVENUE' else 'Expense','Account Code':r['Account Code'],'Account Name':r['Account Name'],'Debit LC':r['Debit LC'],'Credit LC':r['Credit LC'],'Amount LC':amount})
    return rows

def balance_sheet_rows(**flt):
    rows=[]
    for r in trial_balance_rows(**flt):
        typ=str(r['Account Type'] or '').upper()
        if typ not in {'ASSET','LIABILITY','CAPITAL','EQUITY'}:continue
        bal=round(r['Debit LC']-r['Credit LC'],2)
        if typ in {'LIABILITY','CAPITAL','EQUITY'}:bal=-bal
        rows.append({'Section':typ,'Account Code':r['Account Code'],'Account Name':r['Account Name'],'Debit LC':r['Debit LC'],'Credit LC':r['Credit LC'],'Balance LC':bal})
    return rows

def gl_detail_rows(**flt):
    out=[]
    for x in _posted_lines(**flt):
        out.append({
          'Date':x['voucher_date'],'Voucher No':x['voucher_no'],'Voucher Type':x['voucher_type'],
          'Account Code':x['account_code'],'Account Name':x['account_name'],'Description':x['description'],
          'Currency':x['currency'],'Debit VC':x['debit_vc'],'Credit VC':x['credit_vc'],
          'Debit LC':x['debit_lc'],'Credit LC':x['credit_lc'],'Source Type':x['source_type'],
          'Source Ref':x['source_ref'],'Job ID':x['job_id'],'Company':x['company'],'Cost Center':x['cost_center']
        })
    return out

def subledger_reconciliation_rows(company=None,period=None,date_from=None,date_to=None,currency=None,status=None,cost_center=None,**_):
    c=connect()
    try:
        records=[dict(r) for r in c.execute("SELECT id,module,external_ref,job_id,status,payload_json FROM gl_records WHERE module IN ('invoice','bills','receipt','payment','wht-deposits','bank-reconciliation') ORDER BY module,id")]
        out=[]
        for r in records:
            p=_payload(r.get('payload_json'));module=r['module']
            if company and str(p.get('Company') or '').lower()!=company.lower():continue
            if cost_center and str(p.get('Cost Center') or p.get('Cost Centre') or '').lower()!=cost_center.lower():continue
            curr=str(p.get('Currency') or '')
            if currency and curr.upper()!=currency.upper():continue
            d=str(p.get('Date') or p.get('Voucher / Inv Date') or p.get('Voucher / Bill Date') or p.get('Deposit Date') or p.get('Reconciled Date') or '')
            if period and d and not d.startswith(period):continue
            if date_from and d and d<date_from:continue
            if date_to and d and d>date_to:continue
            amount=0.0
            for k in ('Invoice Amount','Payment Amount','Receipt Amount','Amount','Net Amount'):
                if p.get(k) not in (None,''):amount=_f(p.get(k));break
            v=c.execute("SELECT * FROM gl_vouchers WHERE source_ref=? AND status='Posted' ORDER BY id LIMIT 1",(r['external_ref'],)).fetchone()
            if not v:
                # Accepted synthetic/legacy source references may be stored in the record source_ref instead of external_ref.
                sr=c.execute('SELECT source_ref FROM gl_records WHERE id=?',(r['id'],)).fetchone()
                if sr and sr['source_ref']:v=c.execute("SELECT * FROM gl_vouchers WHERE source_ref=? AND status='Posted' ORDER BY id LIMIT 1",(sr['source_ref'],)).fetchone()
            voucher_amount=max(_f(v['total_debit']),_f(v['total_credit'])) if v else 0.0
            linked=bool(v)
            diff=round(amount-voucher_amount,2) if linked and amount else (0.0 if linked else amount)
            recon='MATCHED' if linked and abs(diff)<0.01 else ('UNLINKED' if not linked else 'DIFFERENCE')
            if status and status.upper() not in {'POSTED','ALL'} and recon!=status.upper():continue
            out.append({'Module':module,'Source Ref':r['external_ref'],'Voucher No':v['voucher_no'] if v else None,'Currency':(v['currency'] if v else curr),'Subledger Amount':round(amount,2),'GL Amount':round(voucher_amount,2),'Difference':diff,'Reconciliation Status':recon,'Job ID':r['job_id']})
        return out
    finally:c.close()

def report_rows(key,**flt):
    if key=='trial-balance-report':return trial_balance_rows(**flt)
    if key=='profit-loss':return profit_loss_rows(**flt)
    if key=='balance-sheet':return balance_sheet_rows(**flt)
    if key=='gl-detail':return gl_detail_rows(**flt)
    if key=='subledger-gl-reconciliation':return subledger_reconciliation_rows(**flt)
    raise HTTPException(404,'Unknown GL report')

def _auth(role,session):
    return actor(role,'view',session)

def _filters(company,period,date_from,date_to,account,cost_center,currency,status):
    return dict(company=company,period=period,date_from=date_from,date_to=date_to,account=account,cost_center=cost_center,currency=currency,status=status or 'Posted')

@router.get('/reports/{key}')
def report(key:str,company:Optional[str]=None,period:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,account:Optional[str]=None,cost_center:Optional[str]=None,currency:Optional[str]=None,status:Optional[str]=None,x_role:str=Header('AUDITOR'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    _auth(x_role,x_m3_session)
    if key not in REPORT_KEYS:raise HTTPException(404,'Unknown GL report')
    flt=_filters(company,period,date_from,date_to,account,cost_center,currency,status)
    rows=report_rows(key,**flt)
    out={'phase':'CLX-045','report':key,'filters':flt,'count':len(rows),'rows':rows,'read_only':True}
    if key=='trial-balance-report':
        totals=_totals(rows);out['totals']=totals;out['balanced_vc']=abs(totals['debit_vc']-totals['credit_vc'])<0.01;out['balanced_lc']=abs(totals['debit_lc']-totals['credit_lc'])<0.01
    if key=='profit-loss':
        out['net_profit_loss_lc']=round(sum((r['Amount LC'] if r['Section']=='Revenue' else -r['Amount LC']) for r in rows),2)
    if key=='subledger-gl-reconciliation':
        out['matched']=sum(1 for r in rows if r['Reconciliation Status']=='MATCHED');out['exceptions']=sum(1 for r in rows if r['Reconciliation Status']!='MATCHED')
    return out

@router.get('/drill/account/{account_code}')
def drill_account(account_code:str,period:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,currency:Optional[str]=None,x_role:str=Header('AUDITOR'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    _auth(x_role,x_m3_session)
    rows=gl_detail_rows(account=account_code,period=period,date_from=date_from,date_to=date_to,currency=currency,status='Posted')
    return {'level':'ACCOUNT','account_code':account_code,'count':len(rows),'rows':rows,'next':'voucher'}

@router.get('/drill/voucher/{voucher_no}')
def drill_voucher(voucher_no:str,x_role:str=Header('AUDITOR'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    _auth(x_role,x_m3_session);c=connect()
    try:
        v=c.execute('SELECT * FROM gl_vouchers WHERE voucher_no=?',(voucher_no,)).fetchone()
        if not v:raise HTTPException(404,'Voucher not found')
        rows=[dict(x) for x in c.execute('SELECT l.*,a.account_name,a.account_type FROM gl_voucher_lines l LEFT JOIN gl_accounts a ON a.account_code=l.account_code WHERE l.voucher_id=? ORDER BY l.line_no',(v['id'],))]
        return {'level':'VOUCHER','voucher':dict(v),'lines':rows,'next':'source','source_type':v['source_type'],'source_ref':v['source_ref']}
    finally:c.close()

@router.get('/drill/source/{module}/{external_ref}')
def drill_source(module:str,external_ref:str,x_role:str=Header('AUDITOR'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    _auth(x_role,x_m3_session);c=connect()
    try:
        r=c.execute('SELECT * FROM gl_records WHERE module=? AND external_ref=?',(module,external_ref)).fetchone()
        if not r:raise HTTPException(404,'Source GL record not found')
        d=dict(r);d['fields']=_payload(d.pop('payload_json',None))
        links=[dict(x) for x in c.execute('SELECT * FROM gl_source_links WHERE gl_record_id=? ORDER BY id',(r['id'],))]
        return {'level':'SOURCE','module':module,'record':d,'links':links}
    finally:c.close()

@router.get('/control-summary')
def control_summary(x_role:str=Header('AUDITOR'),x_m3_session:Optional[str]=Header(None,alias='X-M3-Session')):
    _auth(x_role,x_m3_session)
    tb=trial_balance_rows(status='Posted');tot=_totals(tb)
    recon=subledger_reconciliation_rows()
    return {'phase':'CLX-045','trial_balance_balanced_vc':abs(tot['debit_vc']-tot['credit_vc'])<0.01,'trial_balance_balanced_lc':abs(tot['debit_lc']-tot['credit_lc'])<0.01,'trial_balance_totals':tot,'subledger_exceptions':sum(1 for r in recon if r['Reconciliation Status']!='MATCHED'),'live_providers':False,'real_money':False}
