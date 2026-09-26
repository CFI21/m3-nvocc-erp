import json,datetime
from .db import connect
from .masterdata import init_schema,audit,j

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def run():
    c=connect();init_schema(c)
    for t in ['md_audit','md_quality_issues','md_aliases','md_usage','md_versions','md_change_requests','md_records','md_sequences','md_config']:c.execute(f'DELETE FROM {t}')
    ts=now(); data={}
    # Mirror canonical operational masters for reference integrity.
    data['customer']=[(r['code'],r['name'],{'name':r['name'],'effective_from':'2026-01-01'}) for r in c.execute('SELECT code,name FROM customers')]
    data['agent']=[(r['code'],r['name'],{'name':r['name'],'effective_from':'2026-01-01'}) for r in c.execute('SELECT code,name FROM agents')]
    data['carrier']=[('MAEU','Maersk',{'name':'Maersk','scac':'MAEU'}),('CMDU','CMA CGM',{'name':'CMA CGM','scac':'CMDU'}),('HLCU','Hapag-Lloyd',{'name':'Hapag-Lloyd','scac':'HLCU'}),('ONEY','ONE',{'name':'ONE','scac':'ONEY'}),('MSCU','MSC',{'name':'MSC','scac':'MSCU'})]
    ports=sorted({x for r in c.execute('SELECT pol,pod FROM jobs') for x in (r['pol'],r['pod'])})
    data['port']=[(p,p,{'name':p,'unlocode':p}) for p in ports]
    data['location']=[('LOC-RTM','Rotterdam Depot',{'name':'Rotterdam Depot','country':'NL'}),('LOC-DXB','Jebel Ali Terminal',{'name':'Jebel Ali Terminal','country':'AE'})]
    data['vessel']=[(f'VES-{r["id"]:03d}',r['name'],{'name':r['name']}) for r in c.execute('SELECT id,name FROM vessels')]
    data['voyage']=[(r['voyage_no'],r['voyage_no'],{'name':r['voyage_no'],'vessel_id':r['vessel_id']}) for r in c.execute('SELECT voyage_no,vessel_id FROM voyages')]
    data['equipment-type']=[('20GP','20 General Purpose',{'name':'20 General Purpose','teu':1}),('40HC','40 High Cube',{'name':'40 High Cube','teu':2}),('40FR','40 Flat Rack',{'name':'40 Flat Rack','teu':2})]
    data['container-type']=[('DRY','Dry Container',{'name':'Dry Container'}),('REEFER','Reefer Container',{'name':'Reefer Container'}),('OOG','Out of Gauge',{'name':'Out of Gauge'})]
    data['commodity']=[('GEN','General Cargo',{'name':'General Cargo'}),('FOOD','Foodstuff',{'name':'Foodstuff'}),('MACH','Machinery',{'name':'Machinery'})]
    data['package']=[('CTN','Carton',{'name':'Carton'}),('PAL','Pallet',{'name':'Pallet'}),('PCS','Pieces',{'name':'Pieces'})]
    data['common-party']=[('CP-CUS','Customer',{'name':'Customer','party_type':'CUSTOMER'}),('CP-AGT','Agent',{'name':'Agent','party_type':'AGENT'}),('CP-CAR','Carrier / Vendor',{'name':'Carrier / Vendor','party_type':'CARRIER'})]
    data['unit']=[('PCS','Pieces',{'name':'Pieces'}),('CTN','Cartons',{'name':'Cartons'}),('PAL','Pallets',{'name':'Pallets'}),('KG','Kilograms',{'name':'Kilograms'})]
    data['sales-person']=[('SP-001','Sales Person 001',{'name':'Sales Person 001','status':'ACTIVE'})]
    data['charge-code']=[('OFR','Ocean Freight',{'name':'Ocean Freight'}),('DOC','Documentation',{'name':'Documentation'}),('THC','Terminal Handling',{'name':'Terminal Handling'})]
    data['tax-code']=[('VAT-NL-21','NL VAT 21%',{'name':'NL VAT 21%','rate':21,'country':'NL'}),('WHT-PK-10','PK WHT 10%',{'name':'PK WHT 10%','rate':10,'country':'PK'})]
    data['currency']=[('USD','US Dollar',{'name':'US Dollar','iso':'USD'}),('EUR','Euro',{'name':'Euro','iso':'EUR'}),('AED','UAE Dirham',{'name':'UAE Dirham','iso':'AED'})]
    data['exchange-rate-type']=[('SPOT','Spot Rate',{'name':'Spot Rate'}),('MONTHLY','Monthly Accounting Rate',{'name':'Monthly Accounting Rate'})]
    data['payment-term']=[('NET30','Net 30 Days',{'name':'Net 30 Days','days':30}),('PREPAID','Prepaid',{'name':'Prepaid','days':0})]
    data['bank']=[('BANK-NL','M3 Test Bank NL',{'name':'M3 Test Bank NL','country':'NL'}),('BANK-AE','M3 Test Bank AE',{'name':'M3 Test Bank AE','country':'AE'})]
    data['bank-account']=[('BA-USD','M3 USD Operating',{'name':'M3 USD Operating','bank':'BANK-NL','currency':'USD'}),('BA-EUR','M3 EUR Operating',{'name':'M3 EUR Operating','bank':'BANK-NL','currency':'EUR'})]
    data['gl-account']=[('1100','Accounts Receivable',{'name':'Accounts Receivable'}),('2000','Accounts Payable',{'name':'Accounts Payable'}),('4000','Freight Revenue',{'name':'Freight Revenue'}),('5000','Freight Cost',{'name':'Freight Cost'})]
    data['cost-center']=[('OPS','Operations',{'name':'Operations'}),('FIN','Finance',{'name':'Finance'})]
    data['profit-center']=[('NVOCC-EU','NVOCC Europe',{'name':'NVOCC Europe'}),('NVOCC-ME','NVOCC Middle East',{'name':'NVOCC Middle East'})]
    data['trade-lane']=[('ASIA-EU','Asia to Europe',{'name':'Asia to Europe'}),('ME-EU','Middle East to Europe',{'name':'Middle East to Europe'})]
    data['service']=[('FCL','Full Container Load',{'name':'Full Container Load'}),('LCL','Less than Container Load',{'name':'Less than Container Load'})]
    data['route']=[('SGSIN-NLRTM','Singapore to Rotterdam',{'name':'Singapore to Rotterdam','pol':'SGSIN','pod':'NLRTM'}),('AEJEA-DEHAM','Jebel Ali to Hamburg',{'name':'Jebel Ali to Hamburg','pol':'AEJEA','pod':'DEHAM'})]
    data['incoterm']=[('FOB','Free On Board',{'name':'Free On Board'}),('CIF','Cost Insurance Freight',{'name':'Cost Insurance Freight'}),('EXW','Ex Works',{'name':'Ex Works'})]
    data['document-type']=[('HBL','House Bill of Lading',{'name':'House Bill of Lading'}),('MBL','Master Bill of Lading',{'name':'Master Bill of Lading'}),('DO','Delivery Order',{'name':'Delivery Order'})]
    data['release-type']=[('ORIGINAL','Original Release',{'name':'Original Release'}),('TELEX','Telex Release',{'name':'Telex Release'}),('EXPRESS','Express Release',{'name':'Express Release'})]
    data['reference-sequence']=[(x,x,{'name':x}) for x in ['JOB','BOOKING','HBL','MBL','VOUCHER','INVOICE','PAYMENT','TRANSACTION']]
    data['configuration']=[('DEFAULT-CURRENCY','Default Currency',{'name':'Default Currency','value':'USD'}),('JOB-REF-DIGITS','Job Reference Digits',{'name':'Job Reference Digits','value':'5'}),('MASTER-APPROVAL','Master Approval Required',{'name':'Master Approval Required','value':'true'})]
    for domain,rows in data.items():
        for key,name,payload in rows:
            c.execute('INSERT INTO md_records(domain,record_key,display_name,payload_json,status,version,effective_from,approved_by,approved_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(domain,key,name,j(payload),'ACTIVE',1,payload.get('effective_from'), 'seed',ts,ts,ts))
            c.execute('INSERT INTO md_versions(domain,record_key,version,payload_json,status,changed_by,change_ref,ts) VALUES(?,?,?,?,?,?,?,?)',(domain,key,1,j(payload),'ACTIVE','seed','SEED',ts))
    for code,prefix,n,width in [('JOB','',50006,5),('BOOKING','M3BKG',50006,5),('HBL','M3HBL',50006,5),('MBL','M3MBL',50006,5),('VOUCHER','M3V',10001,6),('INVOICE','M3INV',10001,6),('PAYMENT','M3PAY',10001,6),('TRANSACTION','M3TXN',10001,6)]:c.execute('INSERT INTO md_sequences(sequence_code,prefix,next_number,width,status,version) VALUES(?,?,?,?,?,1)',(code,prefix,n,width,'ACTIVE'))
    for k,v,t,scope in [('DEFAULT_CURRENCY','USD','TEXT','GLOBAL'),('REFERENCE_JOB_DIGITS','5','INTEGER','GLOBAL'),('MASTER_APPROVAL_REQUIRED','true','BOOLEAN','GLOBAL'),('PORT_CODE_STANDARD','UNLOCODE','TEXT','GLOBAL'),('DELETE_MODE','DEACTIVATE_ONLY','TEXT','GLOBAL')]:c.execute('INSERT INTO md_config(config_key,config_value,value_type,scope,status,version) VALUES(?,?,?,?,?,1)',(k,v,t,scope,'ACTIVE'))
    for r in c.execute('SELECT j.job_ref,c.code customer_code,a.code agent_code,j.pol,j.pod FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN agents a ON a.id=j.agent_id'):
        refs=[('reference-sequence','JOB','JOB',r['job_ref']),('customer',r['customer_code'],'JOB',r['job_ref']),('agent',r['agent_code'],'JOB',r['job_ref']),('port',r['pol'],'JOB_POL',r['job_ref']),('port',r['pod'],'JOB_POD',r['job_ref'])]
        for x in refs:c.execute('INSERT OR IGNORE INTO md_usage(domain,record_key,source_module,source_ref) VALUES(?,?,?,?)',x)
    c.execute('INSERT INTO md_quality_issues(issue_ref,domain,record_key,issue_code,severity,detail,status,created_at) VALUES(?,?,?,?,?,?,?,?)',('MDQ-001','customer','CLX-CUS-002','TAX_PROFILE_REVIEW','LOW','Synthetic data-quality review item','OPEN',ts))
    audit(c,'seed','MASTER_DATA_SEED',after={'domains':len(data),'sequences':8});c.close()
if __name__=='__main__':run()