import os,json
from fastapi import HTTPException

def enabled():
    return os.getenv('M3_ITEM10_TAX_FX_GOVERNANCE_ENABLED','false').lower()=='true'

def _num(v):
    try:return float(v)
    except:return None

def tax_code_record(conn,code):
    if not code:return None
    r=conn.execute("SELECT record_key,payload_json,status FROM md_records WHERE domain='tax-code' AND record_key=?",(code,)).fetchone()
    if not r or r['status']!='ACTIVE': raise HTTPException(422,{'code':'TAX_CODE_INVALID','tax_code':code})
    p=json.loads(r['payload_json'])
    return {'tax_code':r['record_key'],'rate':float(p.get('rate') or 0),'jurisdiction':p.get('country'),'name':p.get('name')}

def treatment(fields,tc):
    explicit=str(fields.get('Tax Treatment') or '').upper().replace(' ','_')
    if explicit:return explicit
    code=(tc or {}).get('tax_code','').upper()
    if code.startswith('WHT-'):return 'WHT'
    if 'RC' in code or 'REVERSE' in code:return 'REVERSE_CHARGE'
    if 'ZERO' in code:return 'ZERO_RATED'
    if 'EXEMPT' in code:return 'EXEMPT'
    return 'STANDARD'

def authoritative_fx(conn,currency,date_text,base_currency='USD'):
    if currency==base_currency:return {'rate':1.0,'source':'BASE','rate_date':date_text}
    r=conn.execute("""SELECT rate,source,rate_date FROM gl_fx_rates WHERE currency=? AND base_currency=?
      AND date(rate_date)<=date(?) AND status='ACTIVE' ORDER BY date(rate_date) DESC LIMIT 1""",
      (currency,base_currency,date_text)).fetchone()
    if not r: raise HTTPException(422,{'code':'FX_RATE_REQUIRED','currency':currency,'base_currency':base_currency,'date':date_text})
    return {'rate':float(r['rate']),'source':r['source'],'rate_date':r['rate_date']}

def validate_fx_input(conn,currency,date_text,supplied_rate=None,base_currency='USD'):
    fx=authoritative_fx(conn,currency,date_text,base_currency)
    if supplied_rate not in (None,''):
        sr=_num(supplied_rate)
        if sr is None or abs(sr-fx['rate'])>0.000001:
            raise HTTPException(422,{'code':'FX_RATE_OVERRIDE_BLOCKED','authoritative_rate':fx['rate'],'supplied_rate':supplied_rate,'source':fx['source'],'rate_date':fx['rate_date']})
    return fx

def validate_tax(conn,fields):
    code=fields.get('Tax Code') or fields.get('WHT Code')
    if not code:
        return None
    tc=tax_code_record(conn,code)
    supplied_jur=fields.get('Tax Jurisdiction') or fields.get('Jurisdiction')
    if supplied_jur and tc['jurisdiction'] and str(supplied_jur).upper()!=str(tc['jurisdiction']).upper():
        raise HTTPException(422,{'code':'TAX_JURISDICTION_MISMATCH','expected':tc['jurisdiction'],'supplied':supplied_jur})
    tr=treatment(fields,tc)
    base=_num(fields.get('Taxable Base') if fields.get('Taxable Base') not in (None,'') else fields.get('Base Amount'))
    amt=_num(fields.get('Tax Amount'))
    expected=0.0 if tr in {'EXEMPT','ZERO_RATED','REVERSE_CHARGE'} else (round((base or 0)*tc['rate']/100,2) if base is not None else None)
    if amt is not None and expected is not None and abs(amt-expected)>0.01:
        raise HTTPException(422,{'code':'TAX_AMOUNT_MISMATCH','expected':expected,'supplied':amt})
    return {**tc,'treatment':tr,'taxable_amount':base,'tax_amount':amt if amt is not None else expected}

def ensure_period_not_tax_filed(conn,date_text):
    p=conn.execute("""SELECT p.* FROM gl_periods p WHERE date(?) BETWEEN date(p.start_date) AND date(p.end_date)""",(date_text,)).fetchone()
    if p and 'tax_filed_at' in p.keys() and p['tax_filed_at']:
        raise HTTPException(409,{'code':'TAX_FILED_PERIOD_LOCK','period_id':p['id'],'tax_filed_at':p['tax_filed_at']})
    return p

def validate_multicurrency_settlement(conn,fields):
    src_cur=fields.get('Source Currency')
    sett_cur=fields.get('Settlement Currency')
    date=fields.get('Date') or '2026-09-23'
    if not src_cur or not sett_cur: raise HTTPException(422,{'code':'SETTLEMENT_CURRENCIES_REQUIRED'})
    src=_num(fields.get('Source Amount')); sett=_num(fields.get('Settlement Amount'))
    if src is None or sett is None: raise HTTPException(422,{'code':'INVALID_SETTLEMENT_AMOUNT'})
    if sett_cur!='USD':
        raise HTTPException(422,{'code':'SETTLEMENT_BASE_CURRENCY_UNSUPPORTED','settlement_currency':sett_cur})
    fx=validate_fx_input(conn,src_cur,date,fields.get('FX Rate'),'USD')
    expected=round(src*fx['rate'],2)
    if abs(sett-expected)>0.02: raise HTTPException(422,{'code':'FX_SETTLEMENT_MISMATCH','expected':expected,'supplied':sett})
    return {**fx,'source_currency':src_cur,'settlement_currency':sett_cur,'source_amount':src,'settlement_amount':sett}

def validate_fx_event_duplicate(conn,event_type,period_id,job_id,currency):
    dup=conn.execute("""SELECT event_ref FROM gl_fx_events WHERE event_type=? AND period_id=? AND COALESCE(job_id,0)=COALESCE(?,0)
      AND currency=? AND status NOT IN ('REVERSED','CANCELLED') LIMIT 1""",(event_type,period_id,job_id,currency)).fetchone()
    if dup: raise HTTPException(409,{'code':'DUPLICATE_FX_EVENT','existing_ref':dup['event_ref']})
