from .hardening_seed import run as hardening_seed_run
from pathlib import Path
import json

def run(conn):
    now='2026-09-23T21:00:00Z'
    accounts=[
      ('1000','Cash','ASSET',None,'USD'),('1100','Bank - Operating','ASSET',None,'USD'),('1200','Accounts Receivable','ASSET',None,'USD'),
      ('1300','Tax Receivable','ASSET',None,'USD'),('2000','Accounts Payable','LIABILITY',None,'USD'),('2100','WHT Payable','LIABILITY',None,'USD'),
      ('2200','Tax Payable','LIABILITY',None,'USD'),('3000','Opening Equity','CAPITAL',None,'USD'),('4000','Freight Revenue','REVENUE',None,'USD'),('4010','Detention / Storage Revenue','REVENUE',None,'USD'),
      ('5000','Carrier Freight Expense','EXPENSE',None,'USD'),('5010','Storage / Detention Expense','EXPENSE',None,'USD'),('6000','Bank Charges','EXPENSE',None,'USD')]
    for a in accounts:conn.execute('INSERT INTO gl_accounts(account_code,account_name,account_type,parent_code,currency) VALUES(?,?,?,?,?)',a)
    for c in [('USD','US Dollar',2,'SPOT'),('EUR','Euro',2,'SPOT'),('GBP','Pound Sterling',2,'SPOT'),('AED','UAE Dirham',2,'FIXED'),('PKR','Pakistan Rupee',2,'SPOT')]:conn.execute('INSERT INTO gl_currencies(code,name,decimals,rate_type) VALUES(?,?,?,?)',c)
    for typ,pfx in [('JV','JV'),('SI','SI'),('PI','PI'),('RC','RC'),('PV','PV'),('RV','RV')]:conn.execute('INSERT INTO gl_sequences(voucher_type,prefix,next_number,approval_required) VALUES(?,?,1,1)',(typ,pfx))
    maps=[('invoice','POST','1200','4000',None),('bills','POST','5000','2000',None),('receipt','POST','1100','1200',None),('payment','POST','2000','1100',None),('detention-collection','POST','1200','4010',None),('storage-cost','POST','5010','2000',None)]
    for m in maps:conn.execute('INSERT INTO gl_account_mappings(source_module,event_type,debit_account_code,credit_account_code,tax_account_code) VALUES(?,?,?,?,?)',m)
    def rec(module,ext,jid,payload,status='Active',stype=None,sref=None):
        cur=conn.execute('INSERT INTO gl_records(module,external_ref,job_id,source_type,source_ref,status,version,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?,?)',(module,ext,jid,stype,sref,status,json.dumps(payload),now,now));return cur.lastrowid
    # Setup screens
    for a in accounts:
        rec('chart-of-accounts','COA-'+a[0],None,{'Account Code':a[0],'Account Name':a[1],'Type':a[2],'Parent':a[3] or '—','Currency':a[4],'Active':'Yes'})
    for c in [('USD','US Dollar',2,'SPOT'),('EUR','Euro',2,'SPOT'),('GBP','Pound Sterling',2,'SPOT'),('AED','UAE Dirham',2,'FIXED'),('PKR','Pakistan Rupee',2,'SPOT')]:rec('currency','CUR-'+c[0],None,{'Currency':c[0],'Name':c[1],'Decimals':str(c[2]),'Rate Type':c[3],'Active':'Yes'})
    for typ,pfx in [('JV','JV'),('SI','SI'),('PI','PI'),('RC','RC'),('PV','PV')]:rec('voucher-properties','VP-'+typ,None,{'Voucher Type':typ,'Prefix':pfx,'Next Number':'1','Approval Required':'Yes','Active':'Yes'})
    openings=[('1000',10000,0),('1100',20000,0),('1200',30000,0),('2000',0,20000),('3000',0,40000)]
    for i,(code,debit,credit) in enumerate(openings,1):
        name=next(a[1] for a in accounts if a[0]==code)
        rec('opening-balance',f'OB-2026-{i:03d}',None,{'Period':'2026-01','Account Code':code,'Account Name':name,'Debit':str(debit),'Credit':str(credit),'Currency':'USD','Status':'Approved'},'Approved')
    for i,m in enumerate(maps[:5],1):rec('account-integration',f'AI-{i:03d}',None,{'Source Module':m[0],'Event':m[1],'Debit Account':m[2],'Credit Account':m[3],'Tax Account':m[4] or '—','Active':'Yes'})
    for i in range(1,6):rec('reconciliation-date-setup',f'RDS-{i:03d}',None,{'Bank Account':'1100','Cut-off Date':f'2026-0{i}-30','Last Reconciled':f'2026-0{i}-29','Status':'Active'})
    for i in range(1,6):rec('payment-requisition-setup',f'PRS-{i:03d}',None,{'Rule':f'PR-LEVEL-{i}','Min Amount':str((i-1)*5000),'Max Amount':str(i*5000),'Approver Role':'GL_MANAGER' if i>2 else 'FINANCE','Levels':str(1 if i<3 else 2),'Active':'Yes'})
    for i in range(1,6):
        rec('cheque-book-stock',f'CBS-{i:03d}',None,{'Book No.':f'CHQBOOK-{i:02d}','Bank Account':'1100','Start No.':str(100000+i*100),'End No.':str(100099+i*100),'Next No.':str(100000+i*100),'Status':'Active'})
        conn.execute('INSERT INTO gl_cheque_books(book_no,bank_account_code,start_no,end_no,next_no,status) VALUES(?,?,?,?,?,?)',(f'CHQBOOK-{i:02d}','1100',100000+i*100,100099+i*100,100000+i*100,'Active'))
    for i,d in enumerate([1,5,10,20,50],1):rec('cash-denomination-record',f'CDR-{i:03d}',None,{'Currency':'USD','Denomination':str(d),'Type':'Note' if d>1 else 'Coin','Active':'Yes'})
    for i,code in enumerate(['4000','5000','4010','5010','6000'],1):
        amt=50000*i;rec('budget',f'BUD-2026-{i:03d}',None,{'Fiscal Year':'2026','Period':str(i),'Account Code':code,'Budget Amount':str(amt),'Actual Amount':str(int(amt*.72)),'Variance':str(int(amt*.28)),'Status':'Approved'},'Approved');conn.execute('INSERT INTO gl_budget_lines(fiscal_year,period,account_code,amount) VALUES(2026,?,?,?)',(i,code,amt))
    amounts={'50001':(4650,3910),'50002':(7200,6120),'50003':(5850,4980),'50004':(3120,2670),'50005':(12400,10850)}
    # transaction records and four accounting vouchers per job
    for idx,jr in enumerate(amounts,1):
        j=conn.execute('SELECT j.*,b.booking_ref,c.name customer_name,a.name agent_name FROM jobs j JOIN bookings b ON b.id=j.booking_id JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id WHERE j.job_ref=?',(jr,)).fetchone(); jid=j['id']; sell,buy=amounts[jr]
        soa=conn.execute("SELECT external_ref FROM transaction_records WHERE module='soa' AND job_id=?",(jid,)).fetchone()['external_ref']; arp=conn.execute("SELECT external_ref FROM transaction_records WHERE module='agent-receipt-pay' AND job_id=?",(jid,)).fetchone()['external_ref']; det=conn.execute("SELECT external_ref FROM transaction_records WHERE module='detention-collection' AND job_id=?",(jid,)).fetchone()['external_ref']; stg=conn.execute("SELECT external_ref FROM transaction_records WHERE module='storage-cost' AND job_id=?",(jid,)).fetchone()['external_ref']
        inv=f'INV-{jr}'; bill=f'BILL-{jr}'; rc=f'RCPT-{jr}'; pay=f'PAY-{jr}'; req=f'PREQ-{jr}'
        txdata=[
          ('invoice',inv,{'Invoice No.':inv,'Date':'2026-09-23','Customer':j['customer_name'],'Job Ref':jr,'Booking Ref':j['booking_ref'],'Currency':'USD','Amount':str(sell),'Tax':'0','Outstanding':'0','Due Date':'2026-10-23','Status':'Posted'},'Posted','JOB',jr),
          ('bills',bill,{'Bill No.':bill,'Date':'2026-09-23','Supplier':'Carrier '+jr,'Job Ref':jr,'Booking Ref':j['booking_ref'],'Currency':'USD','Amount':str(buy),'Tax':'0','Outstanding':'0','Due Date':'2026-10-23','Status':'Posted'},'Posted','STORAGE_COST',stg),
          ('receipt',rc,{'Receipt No.':rc,'Date':'2026-09-24','Payer':j['customer_name'],'Job Ref':jr,'SOA Ref':soa,'Invoice Ref':inv,'Currency':'USD','Amount':str(sell),'Bank/Cash':'Bank','Reference':arp,'Status':'Posted'},'Posted','AGENT_SOA',soa),
          ('payment',pay,{'Payment No.':pay,'Date':'2026-09-25','Payee':'Carrier '+jr,'Job Ref':jr,'SOA Ref':soa,'Bill Ref':bill,'Currency':'USD','Amount':str(buy),'Bank/Cash':'Bank','Reference':arp,'Status':'Posted'},'Posted','AGENT_RECEIPT_PAY',arp),
          ('payment-requisition',req,{'Requisition No.':req,'Date':'2026-09-24','Payee':'Carrier '+jr,'Job Ref':jr,'Bill Ref':bill,'Currency':'USD','Amount':str(buy),'Purpose':'Carrier freight settlement','Approval Level':'2','Requested By':'FINANCE','Status':'Approved'},'Approved','BILL',bill),
        ]
        ids={}
        for module,ext,payl,stat,stype,sref in txdata:ids[module]=rec(module,ext,jid,payl,stat,stype,sref)
        def voucher(vtype,src_module,src_ref,dr,cr,amt,record_source):
            # Voucher screen record and engine voucher
            row=rec('voucher',f'{vtype}-{jr}',jid,{'Voucher No.':f'{vtype}-{jr}','Date':'2026-09-23','Voucher Type':vtype,'Source':src_module,'Source Ref':src_ref,'Job Ref':jr,'Currency':'USD','Debit Account':dr,'Credit Account':cr,'Amount':str(amt),'Debit':str(amt),'Credit':str(amt),'Narration':f'{src_module} posting {src_ref}','Status':'Posted'},'Posted',src_module,src_ref)
            cur=conn.execute('INSERT INTO gl_vouchers(gl_record_id,voucher_no,voucher_type,voucher_date,currency,status,source_type,source_ref,job_id,total_debit,total_credit,version,posted_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)',(row,f'{vtype}-{jr}',vtype,'2026-09-23','USD','Posted',src_module,src_ref,jid,amt,amt,now,now));vid=cur.lastrowid
            conn.execute('INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)',(vid,1,dr,amt,0,src_ref,jid));conn.execute('INSERT INTO gl_voucher_lines(voucher_id,line_no,account_code,debit,credit,description,job_id) VALUES(?,?,?,?,?,?,?)',(vid,2,cr,0,amt,src_ref,jid));conn.execute('INSERT INTO gl_source_links(gl_record_id,voucher_id,source_type,source_ref,job_id) VALUES(?,?,?,?,?)',(row,vid,src_module,src_ref,jid));return row,vid
        voucher('SI','invoice',inv,'1200','4000',sell,ids['invoice']);voucher('PI','bills',bill,'5000','2000',buy,ids['bills']);voucher('RC','receipt',rc,'1100','1200',sell,ids['receipt']);voucher('PV','payment',pay,'2000','1100',buy,ids['payment'])
        # Link source documents to their voucher(s) and operational charges
        for m in ['invoice','bills','receipt','payment','payment-requisition']:
            sr=conn.execute('SELECT source_type,source_ref FROM gl_records WHERE id=?',(ids[m],)).fetchone();conn.execute('INSERT OR IGNORE INTO gl_source_links(gl_record_id,voucher_id,source_type,source_ref,job_id) VALUES(?,?,?,?,?)',(ids[m],None,sr['source_type'] or m,sr['source_ref'] or jr,jid))
        conn.execute('INSERT OR IGNORE INTO gl_source_links(gl_record_id,voucher_id,source_type,source_ref,job_id) VALUES(?,?,?,?,?)',(ids['invoice'],None,'DETENTION_COLLECTION',det,jid))
        # other GL transaction screens
        rec('bank-reconciliation',f'RECON-{jr}',jid,{'Recon No.':f'RECON-{jr}','Bank Account':'1100','Period':'2026-09','Book Balance':str(sell-buy),'Bank Balance':str(sell-buy),'Difference':'0','Statement Ref':f'STMT-{jr}','Reconciled Date':'2026-09-30','Status':'Reconciled'},'Reconciled','BANK',f'STMT-{jr}')
        rec('cheques-opening',f'CHQ-{jr}',jid,{'Cheque No.':f'{100000+idx}','Book No.':'CHQBOOK-01','Payee':'Carrier '+jr,'Amount':str(buy),'Currency':'USD','Opening Date':'2026-09-24','Payment Ref':pay,'Status':'Open'},'Open','PAYMENT',pay)
        rec('cheque-delivery-marking',f'CHQD-{jr}',jid,{'Cheque No.':f'{100000+idx}','Payee':'Carrier '+jr,'Delivery Date':'2026-09-26','Received By':'Carrier Representative','ID / Reference':f'ID-{jr}','Payment Ref':pay,'Status':'Delivered'},'Delivered','PAYMENT',pay)
        rec('cheque-deposit-marking',f'CHQDEP-{jr}',jid,{'Cheque No.':f'{200000+idx}','Bank Account':'1100','Deposit Date':'2026-09-26','Amount':str(sell),'Currency':'USD','Receipt Ref':rc,'Deposit Slip':f'DS-{jr}','Status':'Deposited'},'Deposited','RECEIPT',rc)
        rec('recall-memo-voucher',f'RMV-{jr}',jid,{'Memo No.':f'RMV-{jr}','Voucher No.':f'PV-{jr}','Reason':'Synthetic recall review','Date':'2026-09-27','Amount':str(buy),'Currency':'USD','Recalled By':'GL_MANAGER','Status':'Open'},'Open','VOUCHER',f'PV-{jr}')
        rec('voucher-approval-dashboard',f'VAD-{jr}',jid,{'Voucher No.':f'SI-{jr}','Type':'SI','Date':'2026-09-23','Amount':str(sell),'Requested By':'GL_ACCOUNTANT','Approver':'GL_MANAGER','Approval Date':'2026-09-23','Comments':'Synthetic approved posting','Status':'Approved'},'Approved','VOUCHER',f'SI-{jr}')
        rec('voucher-history',f'VH-{jr}',jid,{'Voucher No.':f'SI-{jr}','Version':'1','Action':'POST','Actor':'FINANCE','Timestamp':now,'Before Hash':'—','After Hash':f'HASH-{jr}','Status':'Immutable'},'Immutable','VOUCHER',f'SI-{jr}')
        rec('wht-deposits',f'WHT-{jr}',jid,{'Deposit No.':f'WHT-{jr}','Tax Type':'WHT','Period':'2026-09','Authority':'Tax Authority','Amount':str(round(buy*.05,2)),'Currency':'USD','Reference':bill,'Deposit Date':'2026-09-30','Status':'Deposited'},'Deposited','BILL',bill)
        base=sell;rate=5;tax=round(base*rate/100,2);rec('tax-tool',f'TAX-{jr}',jid,{'Calculation Ref':f'TAX-{jr}','Tax Type':'VAT/WHT Test','Base Amount':str(base),'Rate':str(rate),'Tax Amount':str(tax),'Currency':'USD','Job Ref':jr,'Document Ref':inv,'Status':'Calculated'},'Calculated','INVOICE',inv)
    hardening_seed_run(conn)