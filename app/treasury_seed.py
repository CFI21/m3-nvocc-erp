from pathlib import Path
import json
from .json_recovery import load_json_or_recover_arrays

HERE=Path(__file__).resolve().parent

def run(conn):
    now='2026-09-23T23:30:00Z'
    treasury,_=load_json_or_recover_arrays(HERE/'treasury_meta.json',['modules'])
    meta=list(treasury.get('modules',[]))
    if 'bank-reconciliation-exception-queue' not in {m['key'] for m in meta}:
        meta.append({'key':'bank-reconciliation-exception-queue','name':'Bank Reconciliation Exception Queue','group':'work-queues','route':'/treasury/work-queues/bank-reconciliation-exception-queue'})
    accounts=[
        ('BANK-USD-01','BANK','Operating USD','Synthetic Bank A','USD',225000,225000,25000),
        ('BANK-EUR-01','BANK','Operating EUR','Synthetic Bank B','EUR',95000,95000,10000),
        ('CASH-USD-RTM','CASH','Petty Cash Rotterdam',None,'USD',3500,3500,500),
    ]
    for a in accounts:
        conn.execute('INSERT INTO treasury_accounts(account_ref,account_type,account_name,bank_name,currency,opening_balance,current_balance,reserved_balance,status) VALUES(?,?,?,?,?,?,?,?,?)',(*a,'Active'))
    jobs=[dict(x) for x in conn.execute("SELECT j.id,j.job_ref,c.name customer_name,a.name agent_name,b.booking_ref FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id JOIN bookings b ON b.id=j.booking_id ORDER BY j.job_ref")]
    amounts={'50001':(4650,3910),'50002':(7200,6120),'50003':(5850,4980),'50004':(3120,2670),'50005':(12400,10850)}
    def rec(module,ext,j,payload,status='Open',amount=0,currency='USD',party_type=None,party_name=None,source_type=None,source_ref=None,maker='maker-finance'):
        cur=conn.execute('INSERT INTO treasury_records(module,external_ref,job_id,party_type,party_name,currency,amount,status,version,maker_id,checker_id,source_type,source_ref,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)',(module,ext,j['id'] if j else None,party_type,party_name,currency,amount,status,maker,None,source_type,source_ref,json.dumps(payload),now,now))
        return cur.lastrowid
    for m in meta:
        key=m['key']
        for idx,j in enumerate(jobs,1):
            jr=j['job_ref']; sell,buy=amounts[jr]; ext=f'TR-{key[:8].upper()}-{jr}'
            if key=='treasury-dashboard':
                p={'Metric':f'Net liquidity {jr}','Value':str(round(sell-buy,2)),'Currency':'USD','Status':'Healthy'}; stat='Healthy'; amt=sell-buy
            elif key=='bank-accounts':
                a=accounts[(idx-1)%2]; p={'Account Ref':a[0],'Bank':a[3],'Account Name':a[2],'IBAN / Account No.':f'TEST-{idx:04d}','Currency':a[4],'Opening Balance':str(a[5]),'Balance':str(a[6]),'Status':'Active'}; stat='Active'; amt=a[6]
            elif key=='cash-accounts':
                p={'Account Ref':'CASH-USD-RTM','Cash Account':'Petty Cash Rotterdam','Office':'RTM','Currency':'USD','Opening Balance':'3500','Balance':'3500','Status':'Active'};stat='Active';amt=3500
            elif key in ('cashbook','bankbook'):
                p={'Transaction Ref':ext,'Date':'2026-09-23','Account Ref':'BANK-USD-01','Bank Account':'BANK-USD-01','Type':'Receipt' if idx%2 else 'Payment','Job Ref':jr,'Party':j['customer_name'],'Source Ref':f'INV-{jr}','Amount':str(sell),'Currency':'USD','Bank Ref':f'BANKREF-{jr}','Narration':'Synthetic treasury ledger movement','Status':'Posted'}; stat='Posted';amt=sell
            elif key=='customer-receipt-allocation':
                p={'Allocation Ref':ext,'Date':'2026-09-24','Customer':j['customer_name'],'Receipt Ref':f'RCPT-{jr}','Invoice Ref':f'INV-{jr}','Job Ref':jr,'Allocated Amount':str(sell),'Currency':'USD','FX Rate':'1','Status':'Allocated'};stat='Allocated';amt=sell
            elif key=='supplier-carrier-payment-allocation':
                p={'Allocation Ref':ext,'Date':'2026-09-25','Supplier / Carrier':f'Carrier {jr}','Payment Ref':f'PAY-{jr}','Bill Ref':f'BILL-{jr}','Job Ref':jr,'Allocated Amount':str(buy),'Currency':'USD','FX Rate':'1','Status':'Allocated'};stat='Allocated';amt=buy
            elif key=='agent-settlement':
                p={'Settlement Ref':ext,'Date':'2026-09-25','Agent':j['agent_name'],'SOA Ref':f'CLX-SOA-0000{idx}','Job Ref':jr,'Debit':str(sell),'Credit':str(buy),'Net Amount':str(sell-buy),'Currency':'USD','Payment / Receipt Ref':f'RCPT-{jr}','Status':'Settled'};stat='Settled';amt=sell-buy
            elif key=='advance-receipts':
                p={'Advance Ref':ext,'Date':'2026-09-23','Customer / Agent':j['customer_name'],'Job Ref':jr,'Amount':'1000','Currency':'USD','Bank / Cash':'BANK-USD-01','Reference':f'ADV-R-{jr}','Unallocated Amount':'250','Status':'Partially Allocated'};stat='Partially Allocated';amt=1000
            elif key=='advance-payments':
                p={'Advance Ref':ext,'Date':'2026-09-23','Supplier / Carrier':f'Carrier {jr}','Job Ref':jr,'Amount':'750','Currency':'USD','Bank / Cash':'BANK-USD-01','Reference':f'ADV-P-{jr}','Unapplied Amount':'150','Status':'Partially Applied'};stat='Partially Applied';amt=750
            elif key=='on-account-receipts-payments':
                p={'Reference':ext,'Type':'Receipt' if idx%2 else 'Payment','Party':j['customer_name'] if idx%2 else f'Carrier {jr}','Date':'2026-09-23','Job Ref':jr,'Amount':'500','Currency':'USD','Bank / Cash':'BANK-USD-01','Unapplied Amount':'500','Status':'On Account'};stat='On Account';amt=500
            elif key=='partial-settlement':
                p={'Settlement Ref':ext,'Date':'2026-09-24','Source Type':'Invoice','Source Ref':f'INV-{jr}','Job Ref':jr,'Source Amount':str(sell),'Settlement Amount':str(round(sell*.4,2)),'Balance':str(round(sell*.6,2)),'Currency':'USD','Status':'Partial'};stat='Partial';amt=round(sell*.4,2)
            elif key=='multi-invoice-settlement':
                p={'Settlement Ref':ext,'Date':'2026-09-24','Party':j['customer_name'],'Invoice / Bill Refs':f'INV-{jr}, INV-{jr}-A','Job Ref':jr,'Total Source Amount':str(sell),'Settlement Amount':str(sell),'Currency':'USD','Allocation Count':'2','Status':'Allocated'};stat='Allocated';amt=sell
            elif key=='multi-currency-settlement':
                p={'Settlement Ref':ext,'Date':'2026-09-24','Party':j['customer_name'],'Job Ref':jr,'Source Ref':f'INV-{jr}','Source Currency':'EUR','Source Amount':'1000','Settlement Currency':'USD','FX Rate':'1.10','Settlement Amount':'1100','FX Difference':'0','Status':'Settled'};stat='Settled';amt=1100
            elif key=='customer-refunds':
                p={'Refund Ref':ext,'Date':'2026-09-26','Customer':j['customer_name'],'Receipt / Credit Ref':f'RCPT-{jr}','Job Ref':jr,'Amount':'75','Currency':'USD','Bank Account':'BANK-USD-01','Reason':'Synthetic overpayment refund','Status':'Approved'};stat='Approved';amt=75
            elif key=='supplier-refunds':
                p={'Refund Ref':ext,'Date':'2026-09-26','Supplier':f'Carrier {jr}','Payment / Debit Ref':f'PAY-{jr}','Job Ref':jr,'Amount':'50','Currency':'USD','Bank Account':'BANK-USD-01','Reason':'Synthetic supplier refund','Status':'Approved'};stat='Approved';amt=50
            elif key=='payment-batches':
                p={'Batch No.':f'PB-20260923-{idx:03d}','Date':'2026-09-23','Payments':f'PAY-{jr}','Currency':'USD','Total Amount':str(buy),'Maker':'maker-ap','Checker':'checker-manager','Release Ref':f'REL-{jr}','Status':'Released'};stat='Released';amt=buy;ext=p['Batch No.']
            elif key=='payment-approval':
                p={'Approval Ref':ext,'Date':'2026-09-23','Batch / Payment Ref':f'PB-20260923-{idx:03d}','Amount':str(buy),'Currency':'USD','Maker':'maker-ap','Checker':'checker-manager','Approval Level':'1','Comments':'Synthetic approval','Status':'Approved'};stat='Approved';amt=buy
            elif key=='payment-release-control':
                p={'Release Ref':ext,'Batch / Payment Ref':f'PB-20260923-{idx:03d}','Bank Account':'BANK-USD-01','Amount':str(buy),'Currency':'USD','Release Date':'2026-09-25','Approval Ref':f'TR-PAYMENT-{jr}','Released By':'treasury-manager','Status':'Released'};stat='Released';amt=buy
            elif key in ('bank-transfer','inter-bank-transfer'):
                p={'Transfer Ref':ext,'Date':'2026-09-23','From Account':'BANK-USD-01','To Account':'BANK-EUR-01','From Bank':'BANK-USD-01','To Bank':'BANK-EUR-01','Amount':'1000','Currency':'USD','FX Rate':'1','Bank Reference':f'BT-{jr}','Charges':'5','Narration':'Synthetic transfer','Status':'Released'};stat='Released';amt=1000
            elif key=='petty-cash':
                p={'Petty Cash Ref':ext,'Date':'2026-09-23','Office':'RTM','Payee':'Local Expense','Job Ref':jr,'Purpose':'Operational petty cash','Amount':'40','Currency':'USD','Cash Account':'CASH-USD-RTM','Status':'Posted'};stat='Posted';amt=40
            elif key=='cash-denomination':
                p={'Count Ref':ext,'Date':'2026-09-23','Cash Account':'CASH-USD-RTM','Currency':'USD','Denomination Detail':'50x20, 20x25, 10x100, 5x200','Counted Total':'3500','Book Balance':'3500','Difference':'0','Counted By':'cashier-01','Status':'Balanced'};stat='Balanced';amt=3500
            elif key=='cheque-lifecycle':
                p={'Cheque No.':f'CHQ-{jr}','Bank Account':'BANK-USD-01','Party':f'Carrier {jr}','Job Ref':jr,'Amount':str(buy),'Currency':'USD','Issue Date':'2026-09-23','Delivery Date':'2026-09-24','Deposit Date':'2026-09-25','Clear Date':'2026-09-27','Stage':'Cleared','Status':'Cleared'};stat='Cleared';amt=buy;ext=p['Cheque No.']
            elif key=='post-dated-cheques':
                p={'Cheque No.':f'PDC-{jr}','Party':j['customer_name'],'Job Ref':jr,'Cheque Date':'2026-10-15','Received / Issued':'Received','Bank':'Synthetic Bank A','Amount':str(sell),'Currency':'USD','Maturity Action':'Deposit','Status':'Pending'};stat='Pending';amt=sell;ext=p['Cheque No.']
            elif key=='bounced-returned-cheques':
                p={'Return Ref':ext,'Cheque No.':f'PDC-{jr}','Party':j['customer_name'],'Job Ref':jr,'Return Date':'2026-10-16','Amount':'250','Currency':'USD','Reason':'Insufficient funds','Charge Amount':'25','Recovery Status':'Open','Status':'Returned'};stat='Returned';amt=250
            elif key=='bank-charges':
                p={'Charge Ref':ext,'Date':'2026-09-23','Bank Account':'BANK-USD-01','Bank Ref':f'BCH-{jr}','Job Ref':jr,'Amount':'15','Currency':'USD','Expense Account':'6000','Narration':'Synthetic bank charge','Status':'Posted'};stat='Posted';amt=15
            elif key=='bank-interest':
                p={'Interest Ref':ext,'Date':'2026-09-23','Bank Account':'BANK-USD-01','Type':'Credit Interest','Job Ref':jr,'Amount':'20','Currency':'USD','Income / Expense Account':'4000','Narration':'Synthetic bank interest','Status':'Posted'};stat='Posted';amt=20
            elif key=='cash-bank-position':
                p={'Account Ref':'BANK-USD-01','Type':'BANK','Currency':'USD','Available Balance':'225000','Reserved':'25000','Net Available':'200000','Status':'Healthy'};stat='Healthy';amt=200000
            elif key=='daily-liquidity':
                p={'Date':'2026-09-23','Currency':'USD','Opening':'225000','Expected Inflows':str(sell),'Expected Outflows':str(buy),'Closing':str(225000+sell-buy),'Minimum Buffer':'100000','Variance To Buffer':str(125000+sell-buy),'Status':'Above Buffer'};stat='Above Buffer';amt=225000+sell-buy
            elif key=='aging-collection-work-queue':
                p={'Customer':j['customer_name'],'Invoice Ref':f'INV-{jr}','Job Ref':jr,'Due Date':'2026-09-20','Days Overdue':'3','Outstanding':str(round(sell*.25,2)),'Currency':'USD','Collector':'AR-TEAM','Next Action':'Customer follow-up','Priority':'HIGH' if idx<3 else 'MEDIUM','Status':'Open'};stat='Open';amt=round(sell*.25,2)
            elif key=='payable-due-date-work-queue':
                p={'Supplier':f'Carrier {jr}','Bill Ref':f'BILL-{jr}','Job Ref':jr,'Due Date':'2026-09-28','Days To Due':'5','Outstanding':str(round(buy*.30,2)),'Currency':'USD','Owner':'AP-TEAM','Next Action':'Schedule payment','Priority':'MEDIUM','Status':'Open'};stat='Open';amt=round(buy*.30,2)
            elif key=='customer-supplier-statement':
                p={'Statement Ref':ext,'Party Type':'Customer','Party':j['customer_name'],'Period From':'2026-09-01','Period To':'2026-09-30','Opening':'0','Debit':str(sell),'Credit':str(round(sell*.75,2)),'Balance':str(round(sell*.25,2)),'Currency':'USD','Status':'Generated'};stat='Generated';amt=round(sell*.25,2)
            elif key=='settlement-history':
                p={'Settlement Ref':ext,'Type':'Customer Receipt','Party':j['customer_name'],'Job Ref':jr,'Source Ref':f'INV-{jr}','Amount':str(sell),'Currency':'USD','FX Rate':'1','Date':'2026-09-24','Voucher Ref':f'RC-{jr}','Status':'Settled'};stat='Settled';amt=sell
            elif key=='unallocated-cash':
                p={'Receipt Ref':f'ADV-R-{jr}','Party':j['customer_name'],'Job Ref':jr,'Date':'2026-09-23','Amount':'1000','Allocated':'750','Unallocated':'250','Currency':'USD','Age Days':'0','Status':'Unallocated'};stat='Unallocated';amt=250
            elif key=='unapplied-payments':
                p={'Payment Ref':f'ADV-P-{jr}','Party':f'Carrier {jr}','Job Ref':jr,'Date':'2026-09-23','Amount':'750','Applied':'600','Unapplied':'150','Currency':'USD','Age Days':'0','Status':'Unapplied'};stat='Unapplied';amt=150
            elif key=='bank-reconciliation-exception-queue':
                p={'Exception Ref':ext,'Bank Account':'BANK-USD-01','Statement Ref':f'BST-{jr}','Date':'2026-09-23','Amount':str(100+idx),'Currency':'USD','Reason':'No exact book match','Suggested Match':f'RCPT-{jr}','Priority':'HIGH' if idx==1 else 'MEDIUM','Owner':'TREASURY','Status':'Open'};stat='Open';amt=100+idx
            else:
                p={'Reference':ext,'Job Ref':jr,'Currency':'USD','Amount':'0','Status':'Open'};stat='Open';amt=0
            rid=rec(key,ext,j,p,stat,amt,'USD','CUSTOMER',j['customer_name'])
            if key=='customer-receipt-allocation': conn.execute('INSERT INTO treasury_allocations(treasury_record_id,source_type,source_ref,allocated_amount,currency,fx_rate,job_id) VALUES(?,?,?,?,?,?,?)',(rid,'INVOICE',f'INV-{jr}',sell,'USD',1,j['id']))
            if key=='supplier-carrier-payment-allocation': conn.execute('INSERT INTO treasury_allocations(treasury_record_id,source_type,source_ref,allocated_amount,currency,fx_rate,job_id) VALUES(?,?,?,?,?,?,?)',(rid,'BILL',f'BILL-{jr}',buy,'USD',1,j['id']))
            if key=='payment-batches':
                cur=conn.execute('INSERT INTO treasury_payment_batches(batch_no,record_id,currency,total_amount,status,maker_id,checker_id,released_by,version,created_at,approved_at,released_at) VALUES(?,?,?,?,?,?,?,?,1,?,?,?)',(ext,rid,'USD',buy,'Released','maker-ap','checker-manager','treasury-manager',now,now,now))
                conn.execute('INSERT INTO treasury_batch_items(batch_id,source_type,source_ref,amount,job_id) VALUES(?,?,?,?,?)',(cur.lastrowid,'PAYMENT',f'PAY-{jr}',buy,j['id']))
            if key in ('cheque-lifecycle','post-dated-cheques'):
                conn.execute('INSERT OR IGNORE INTO treasury_cheques(cheque_no,record_id,bank_account_ref,party,amount,currency,cheque_date,stage,status) VALUES(?,?,?,?,?,?,?,?,?)',(p['Cheque No.'],rid,'BANK-USD-01',p['Party'],amt,'USD',p.get('Cheque Date') or p.get('Issue Date'),'PDC' if key=='post-dated-cheques' else 'Cleared',stat))
    feed=[('BST-001','2026-09-23',4650,'Customer receipt 50001','MATCHED','RCPT-50001',None),('BST-002','2026-09-23',-3910,'Carrier payment 50001','MATCHED','PAY-50001',None),('BST-003','2026-09-23',125.55,'Unknown incoming transfer','UNMATCHED',None,'NO_EXACT_MATCH'),('BST-004','2026-09-23',-98.10,'Unknown bank debit','EXCEPTION',None,'REFERENCE_MISSING')]
    for ref,d,amt,desc,st,src,reason in feed:
        conn.execute('INSERT INTO treasury_bank_feed_items(bank_account_ref,statement_ref,txn_date,amount,currency,description,match_status,matched_source_ref,exception_reason) VALUES(?,?,?,?,?,?,?,?,?)',('BANK-USD-01',ref,d,amt,'USD',desc,st,src,reason))