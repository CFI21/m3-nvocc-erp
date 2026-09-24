import json

def run(conn):
    now='2026-09-23T22:00:00Z'
    def rec(module,ext,jid,payload,status='Active',stype=None,sref=None):
        cur=conn.execute('INSERT INTO gl_records(module,external_ref,job_id,source_type,source_ref,status,version,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?,?)',(module,ext,jid,stype,sref,status,json.dumps(payload),now,now))
        return cur.lastrowid
    conn.execute("INSERT INTO gl_fiscal_years(fiscal_year,start_date,end_date,status) VALUES(2026,'2026-01-01','2026-12-31','OPEN')")
    fyid=conn.execute('SELECT id FROM gl_fiscal_years WHERE fiscal_year=2026').fetchone()['id']
    import calendar
    for m in range(1,13):
        end=calendar.monthrange(2026,m)[1]
        status='CLOSED' if m<=8 else ('OPEN' if m in (9,10) else 'FUTURE')
        conn.execute('INSERT INTO gl_periods(fiscal_year_id,period_no,start_date,end_date,status,backdate_allowed_until) VALUES(?,?,?,?,?,?)',(fyid,m,f'2026-{m:02d}-01',f'2026-{m:02d}-{end:02d}',status,f'2026-{m:02d}-{end:02d}'))
        pid=conn.execute('SELECT id FROM gl_periods WHERE fiscal_year_id=? AND period_no=?',(fyid,m)).fetchone()['id']
        earliest=f'2026-{m:02d}-10' if m==9 else f'2026-{m:02d}-01'
        conn.execute('INSERT INTO gl_backdate_controls(period_id,earliest_posting_date,override_role,active) VALUES(?,?,?,1)',(pid,earliest,'ADMIN'))
    rec('fiscal-year','FY-2026',None,{'Fiscal Year':'2026','Start Date':'2026-01-01','End Date':'2026-12-31','Status':'OPEN','Version':'1'},'OPEN')
    for m in range(8,13):
        pr=conn.execute('SELECT * FROM gl_periods WHERE fiscal_year_id=? AND period_no=?',(fyid,m)).fetchone()
        rec('accounting-periods',f'PER-2026-{m:02d}',None,{'Fiscal Year':'2026','Period':str(m),'Start Date':pr['start_date'],'End Date':pr['end_date'],'Status':pr['status'],'Backdate From':('2026-09-10' if m==9 else pr['start_date'])},pr['status'])
    rules=[('JV',0,9999.99,1,'GL_MANAGER',1),('JV',10000,None,2,'ADMIN',1),('*',0,None,1,'GL_MANAGER',1)]
    for i,r in enumerate(rules,1):
        conn.execute('INSERT INTO gl_approval_rules(voucher_type,min_amount,max_amount,required_levels,checker_role,maker_checker,active) VALUES(?,?,?,?,?,?,1)',r)
        rec('journal-approval-levels',f'APR-{i:03d}',None,{'Voucher Type':r[0],'Min Amount':str(r[1]),'Max Amount':str(r[2] if r[2] is not None else 'No Limit'),'Required Levels':str(r[3]),'Checker Role':r[4],'Maker/Checker':'Yes','Active':'Yes'})
    for c,rate in [('EUR',1.10),('GBP',1.27),('AED',0.2723),('PKR',0.00358),('USD',1.0)]:
        conn.execute("INSERT INTO gl_fx_rates(rate_date,currency,base_currency,rate,source,status) VALUES('2026-09-23',?,'USD',?,'TEST_RATE','ACTIVE')",(c,rate))
    jobs=[dict(r) for r in conn.execute('SELECT j.id,j.job_ref,c.id customer_id,c.name customer_name FROM jobs j JOIN customers c ON c.id=j.customer_id ORDER BY j.job_ref')]
    for i,j in enumerate(jobs,1):
        jid=j['id'];jr=j['job_ref']
        invp=json.loads(conn.execute("SELECT payload_json FROM gl_records WHERE module='invoice' AND job_id=?",(jid,)).fetchone()['payload_json'])
        bp=json.loads(conn.execute("SELECT payload_json FROM gl_records WHERE module='bills' AND job_id=?",(jid,)).fetchone()['payload_json'])
        sell=float(invp['Amount']);buy=float(bp['Amount'])
        ar=round(sell*(0.10+0.03*i),2);ap=round(buy*(0.08+0.02*i),2)
        conn.execute("INSERT INTO gl_ar_open_items(customer_id,job_id,source_ref,document_date,due_date,currency,original_amount,outstanding,status) VALUES(?,?,?,?,?,'USD',?,?,'OPEN')",(j['customer_id'],jid,f'AR-{jr}','2026-08-15',f'2026-09-{10+i:02d}',sell,ar))
        conn.execute("INSERT INTO gl_ap_open_items(supplier_name,job_id,source_ref,document_date,due_date,currency,original_amount,outstanding,status) VALUES(?,?,?,?,?,'USD',?,?,'OPEN')",(f'Carrier {jr}',jid,f'AP-{jr}','2026-08-20',f'2026-09-{12+i:02d}',buy,ap))
        limit=15000+5000*i
        conn.execute("INSERT INTO gl_credit_limits(customer_id,currency,credit_limit,exposure,on_hold) VALUES(?,'USD',?,?,?)",(j['customer_id'],limit,ar,1 if ar>limit else 0))
        rec('credit-note',f'CN-{jr}',jid,{'Credit Note No.':f'CN-{jr}','Date':'2026-09-23','Customer':j['customer_name'],'Job Ref':jr,'Invoice Ref':f'INV-{jr}','Currency':'USD','Amount':str(round(sell*.02,2)),'Reason':'Synthetic rate adjustment','Status':'Approved'},'Approved','invoice',f'INV-{jr}')
        rec('debit-note',f'DN-{jr}',jid,{'Debit Note No.':f'DN-{jr}','Date':'2026-09-23','Supplier':f'Carrier {jr}','Job Ref':jr,'Bill Ref':f'BILL-{jr}','Currency':'USD','Amount':str(round(buy*.015,2)),'Reason':'Synthetic carrier correction','Status':'Approved'},'Approved','bills',f'BILL-{jr}')
        available=limit-ar
        rec('customer-credit-control',f'CCC-{jr}',jid,{'Customer':j['customer_name'],'Credit Limit':str(limit),'Exposure':str(ar),'Available':str(round(available,2)),'Hold':'No','Status':'Within Limit'},'Within Limit')
        rec('supplier-payable-control',f'SPC-{jr}',jid,{'Supplier / Carrier':f'Carrier {jr}','Outstanding':str(ap),'Due':str(ap),'Overdue':str(ap if i<3 else 0),'Currency':'USD','Status':'Open'},'Open')
        curr=['EUR','GBP','AED','PKR','EUR'][i-1];rate=conn.execute('SELECT rate FROM gl_fx_rates WHERE currency=?',(curr,)).fetchone()['rate'];exposure=1000*i;gl=round(exposure*(rate-rate*.97),2)
        period9=conn.execute('SELECT id FROM gl_periods WHERE fiscal_year_id=? AND period_no=9',(fyid,)).fetchone()['id']
        conn.execute('INSERT INTO gl_fx_events(event_ref,event_type,period_id,job_id,currency,foreign_amount,old_rate,new_rate,gain_loss,voucher_no,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(f'FXU-{jr}','UNREALIZED',period9,jid,curr,exposure,rate*.97,rate,gl,None,'Calculated'))
        conn.execute('INSERT INTO gl_fx_events(event_ref,event_type,period_id,job_id,currency,foreign_amount,old_rate,new_rate,gain_loss,voucher_no,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(f'FXR-{jr}','REALIZED',period9,jid,curr,exposure*.25,rate*.95,rate,round(exposure*.25*(rate-rate*.95),2),f'JV-FX-{jr}','Posted'))
        rec('fx-revaluation',f'FXREV-{jr}',jid,{'Period':'2026-09','Currency':curr,'Rate':str(rate),'Exposure':str(exposure),'Unrealized Gain/Loss':str(gl),'Voucher Ref':'—','Status':'Calculated'},'Calculated')
        acr=round(buy*.05,2);prep=round(buy*.03,2)
        arid=rec('accruals',f'ACR-{jr}',jid,{'Accrual No.':f'ACR-{jr}','Period':'2026-09','Job Ref':jr,'Account':'5010','Offset Account':'2000','Amount':str(acr),'Reverse Date':'2026-10-01','Narration':'Month-end carrier accrual','Status':'Approved'},'Approved')
        prid=rec('prepayments',f'PREP-{jr}',jid,{'Prepayment No.':f'PREP-{jr}','Period':'2026-09','Job Ref':jr,'Account':'1300','Expense Account':'6000','Amount':str(prep),'Amortized':'0','Balance':str(prep),'Status':'Active'},'Active')
        rec('month-end-journals',f'MEJ-{jr}',jid,{'Journal No.':f'MEJ-{jr}','Period':'2026-09','Type':'Accrual','Job Ref':jr,'Debit Account':'5010','Credit Account':'2000','Amount':str(acr),'Debit':str(acr),'Credit':str(acr),'Approval':'Approved','Status':'Ready'},'Ready')
        conn.execute('INSERT INTO gl_accrual_schedules(record_id,job_id,period_id,amount,accrued_account,offset_account,reverse_date,status) VALUES(?,?,?,?,?,?,?,?)',(arid,jid,period9,acr,'5010','2000','2026-10-01','OPEN'))
        conn.execute('INSERT INTO gl_prepayment_schedules(record_id,job_id,period_id,amount,amortized,asset_account,expense_account,status) VALUES(?,?,?,?,?,?,?,?)',(prid,jid,period9,prep,0,'1300','6000','ACTIVE'))
        conn.execute('INSERT INTO gl_tax_postings(posting_ref,tax_type,source_ref,job_id,taxable_amount,tax_amount,debit_account,credit_account,voucher_no,status) VALUES(?,?,?,?,?,?,?,?,?,?)',(f'TAXP-{jr}','VAT',f'INV-{jr}',jid,sell,round(sell*.05,2),'1300','2200',f'JV-TAX-{jr}','Posted'))
        conn.execute('INSERT INTO gl_tax_postings(posting_ref,tax_type,source_ref,job_id,taxable_amount,tax_amount,debit_account,credit_account,voucher_no,status) VALUES(?,?,?,?,?,?,?,?,?,?)',(f'WHTP-{jr}','WHT',f'BILL-{jr}',jid,buy,round(buy*.05,2),'2000','2100',f'JV-WHT-{jr}','Posted'))
        b=[0,0,0,0,0];b[min(i-1,4)]=ar
        rec('ar-aging',f'ARA-{jr}',jid,{'Customer':j['customer_name'],'Current':str(b[0]),'1-30':str(b[1]),'31-60':str(b[2]),'61-90':str(b[3]),'90+':str(b[4]),'Total':str(ar)},'Open')
        pb=[0,0,0,0,0];pb[min(i,4)]=ap
        rec('ap-aging',f'APA-{jr}',jid,{'Supplier / Carrier':f'Carrier {jr}','Current':str(pb[0]),'1-30':str(pb[1]),'31-60':str(pb[2]),'61-90':str(pb[3]),'90+':str(pb[4]),'Total':str(ap)},'Open')
        rev=conn.execute("SELECT COALESCE(SUM(l.credit-l.debit),0) x FROM gl_voucher_lines l JOIN gl_vouchers v ON v.id=l.voucher_id WHERE v.job_id=? AND l.account_code LIKE '4%' AND v.status='Posted'",(jid,)).fetchone()['x']
        cost=conn.execute("SELECT COALESCE(SUM(l.debit-l.credit),0) x FROM gl_voucher_lines l JOIN gl_vouchers v ON v.id=l.voucher_id WHERE v.job_id=? AND l.account_code LIKE '5%' AND v.status='Posted'",(jid,)).fetchone()['x']
        margin=round(rev-cost,2);op=round(sell-buy,2)
        rec('job-profitability-reconciliation',f'JPR-{jr}',jid,{'Job Ref':jr,'Revenue':str(rev),'Cost':str(cost),'Margin':str(margin),'Operational Margin':str(op),'Difference':str(round(margin-op,2)),'Status':'Reconciled' if abs(margin-op)<.01 else 'Review'},'Reconciled' if abs(margin-op)<.01 else 'Review')
        rec('subledger-gl-reconciliation',f'SGL-{jr}',jid,{'Subledger':'Invoice/Bill','Source Ref':f'INV-{jr} / BILL-{jr}','Job Ref':jr,'Subledger Amount':str(op),'GL Amount':str(margin),'Difference':str(round(op-margin,2)),'Status':'Reconciled'},'Reconciled')
    posted=[dict(r) for r in conn.execute("SELECT id,voucher_no,total_debit FROM gl_vouchers WHERE status='Posted' ORDER BY id LIMIT 5")]
    for i in range(1,6):
        amt=float(posted[i-1]['total_debit']) if i<=2 else 100*i
        ref=f'STMT-006-{i:03d}'
        conn.execute('INSERT INTO gl_bank_statement_items(statement_ref,bank_account_code,txn_date,description,amount,currency,matched) VALUES(?,?,?,?,?,?,?)',(ref,'1100',f'2026-09-{15+i:02d}',f'Synthetic bank item {i}',amt,'USD',1 if i<=2 else 0))
        bid=conn.execute('SELECT id FROM gl_bank_statement_items WHERE statement_ref=?',(ref,)).fetchone()['id']
        if i<=2:conn.execute('INSERT INTO gl_bank_matches(statement_item_id,voucher_id,match_type,matched_amount,actor_role,ts) VALUES(?,?,?,?,?,?)',(bid,posted[i-1]['id'],'AUTO',amt,'SYSTEM',now))
        rec('bank-matching',f'BM-{i:03d}',None,{'Statement Ref':ref,'Book Ref':posted[i-1]['voucher_no'] if i<=2 else '—','Date':f'2026-09-{15+i:02d}','Bank Amount':str(amt),'Book Amount':str(amt if i<=2 else 0),'Difference':str(0 if i<=2 else amt),'Match Type':'AUTO' if i<=2 else '—','Status':'Matched' if i<=2 else 'Unmatched'},'Matched' if i<=2 else 'Unmatched')
        if i>=3:rec('unmatched-bank-items',f'UBI-{i:03d}',None,{'Statement Ref':ref,'Date':f'2026-09-{15+i:02d}','Description':f'Synthetic bank item {i}','Amount':str(amt),'Currency':'USD','Status':'Unmatched'},'Unmatched')
    period9=conn.execute('SELECT id FROM gl_periods WHERE fiscal_year_id=? AND period_no=9',(fyid,)).fetchone()['id']
    checks=[('SUBLEDGER','Subledger to GL reconciled','GL_MANAGER'),('BANK','Bank reconciliation complete','FINANCE'),('AR_AP','AR/AP aging reviewed','FINANCE'),('FX','FX revaluation completed','GL_ACCOUNTANT'),('TAX','Tax/WHT postings reviewed','FINANCE'),('JOBS','Job profitability reconciled','GL_MANAGER'),('TB','Trial balance balanced','GL_MANAGER')]
    for code,name,owner in checks:
        conn.execute('INSERT INTO gl_period_close_checks(period_id,check_code,check_name,owner_role,status,evidence,updated_at) VALUES(?,?,?,?,?,?,?)',(period9,code,name,owner,'PASS','Synthetic CLX-006 acceptance evidence',now))
        rec('period-close-checklist',f'PCC-{code}',None,{'Period':'2026-09','Check':name,'Owner':owner,'Status':'PASS','Evidence':'Synthetic CLX-006 acceptance evidence'},'PASS')
    tb=[dict(r) for r in conn.execute("SELECT a.account_code,a.account_name,ROUND(COALESCE(SUM(CASE WHEN v.status='Posted' THEN l.debit ELSE 0 END),0),2) debit,ROUND(COALESCE(SUM(CASE WHEN v.status='Posted' THEN l.credit ELSE 0 END),0),2) credit FROM gl_accounts a LEFT JOIN gl_voucher_lines l ON l.account_code=a.account_code LEFT JOIN gl_vouchers v ON v.id=l.voucher_id GROUP BY a.id ORDER BY a.account_code")]
    for i,r in enumerate(tb,1):rec('trial-balance-report',f'TBR-{i:03d}',None,{'Account Code':r['account_code'],'Account Name':r['account_name'],'Debit':str(r['debit']),'Credit':str(r['credit']),'Balance':str(round(r['debit']-r['credit'],2))},'Generated')
    for i,r in enumerate([x for x in tb if x['account_code'].startswith(('4','5','6'))],1):
        amt=round(r['credit']-r['debit'],2) if r['account_code'].startswith('4') else round(r['debit']-r['credit'],2)
        rec('profit-loss',f'PL-{i:03d}',None,{'Section':'Revenue' if r['account_code'].startswith('4') else 'Expense','Account Code':r['account_code'],'Account Name':r['account_name'],'Amount':str(amt)},'Generated')
    for i,r in enumerate([x for x in tb if x['account_code'].startswith(('1','2'))],1):
        amt=round(r['debit']-r['credit'],2) if r['account_code'].startswith('1') else round(r['credit']-r['debit'],2)
        rec('balance-sheet',f'BS-{i:03d}',None,{'Section':'Assets' if r['account_code'].startswith('1') else 'Liabilities','Account Code':r['account_code'],'Account Name':r['account_name'],'Amount':str(amt)},'Generated')
    lines=[dict(r) for r in conn.execute("SELECT v.voucher_no,v.voucher_date,l.account_code,j.job_ref,l.debit,l.credit,l.description FROM gl_voucher_lines l JOIN gl_vouchers v ON v.id=l.voucher_id LEFT JOIN jobs j ON j.id=l.job_id WHERE v.status='Posted' ORDER BY v.id,l.line_no LIMIT 25")]
    for i,r in enumerate(lines,1):rec('gl-detail',f'GLD-{i:03d}',None,{'Voucher No.':r['voucher_no'],'Date':r['voucher_date'],'Account':r['account_code'],'Job Ref':r['job_ref'] or '—','Debit':str(r['debit']),'Credit':str(r['credit']),'Description':r['description'] or ''},'Generated')