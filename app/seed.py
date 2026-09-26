from pathlib import Path
import json,datetime,re,hashlib,uuid
from .db import connect,DB_PATH
from .gl_seed import run as gl_seed_run
from .treasury_seed import run as treasury_seed_run
from .integration_seed import run as integration_seed_run
from .json_recovery import extract_array_objects
HERE=Path(__file__).resolve().parent
META=json.loads((HERE/'module_meta.json').read_text()); PRIMARY=META['primary_keys']
def extref(module,row,i,job):
    k=PRIMARY.get(module); v=str(row.get(k,'')).strip() if k else ''
    if module=='vessel-lock': v=f"{row.get('Vessel','VSL')}-{row.get('Voyage',job)}"
    if module=='planning': v=f"{row.get('Voyage',job)}-{row.get('POL','POL')}-{row.get('POD','POD')}"
    if module=='storage-cost': v=f"STG-{job}-{i+1:03d}"
    return v or f"CLX-{module.upper()}-{job}-{i+1:03d}"
def num(v):
    try:return float(v or 0)
    except:return 0.0
def seed_support(conn,module,tid,row,jr,jid,container_id,now):
    if module=='special-rates-request':
        conn.execute('INSERT INTO special_rate_workflow(transaction_id,stage,carrier_response,approved_rate,quote_ref,booking_ref,updated_at) VALUES(?,?,?,?,?,?,?)',(tid,row.get('Stage','Requested'),row.get('Carrier Response'),num(row.get('Approved Rate')) or None,row.get('Quote Ref'),row.get('Booking Ref'),now))
    elif module=='split-bl':
        for n in (1,2):
            conn.execute('INSERT INTO split_bl_allocations(transaction_id,child_bill_no,container_no,packages,weight,measurement) VALUES(?,?,?,?,?,?)',(tid,row.get(f'Child B/L {n}'),row.get('Container',''),num(row.get(f'Child {n} Packages')),num(row.get(f'Child {n} Weight')),num(row.get(f'Child {n} Measurement'))))
    elif module=='switch-bl' and row.get('Approval')=='Approved':
        raw=f"{tid}|{row.get('Original B/L')}|{row.get('Switch B/L')}|{jr}|seed"; h=hashlib.sha256(raw.encode()).hexdigest()
        conn.execute('INSERT INTO switch_bl_history(transaction_id,job_id,ts,original_bill_no,switch_bill_no,original_parties_json,new_parties_json,approved_by,confidentiality,immutable_hash) VALUES(?,?,?,?,?,?,?,?,1,?)',(tid,jid,now,row.get('Original B/L'),row.get('Switch B/L'),json.dumps({'shipper':row.get('Original Shipper'),'consignee':row.get('Original Consignee')}),json.dumps({'shipper':row.get('New Shipper'),'consignee':row.get('New Consignee'),'notify':row.get('New Notify')}),row.get('Approved By') or 'CLX Supervisor',h))
def _load_seed():
    path=HERE/'seed.json'; text=path.read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        modules=META['modules']
        data={}
        crt=extract_array_objects(text,'crt')
        if len(crt)<5:
            raise RuntimeError('M3_SEED_RECOVERY_REQUIRES_FIVE_COMPLETE_CRT_ROWS')
        jobs={}
        for i,row in enumerate(crt[:5],1):
            jr=str(row.get('Job Ref') or f'5000{i}')
            jobs[jr]={
              'customer':row.get('Customer') or f'Synthetic Customer {jr}',
              'agent':row.get('Agent') or f'CLX-AGT-{jr}',
              'booking':row.get('Booking Ref') or f'CLX-BKG-{jr}',
              'vessel':row.get('Vessel') or f'Synthetic Vessel {jr}',
              'voyage':row.get('Voyage') or f'VOY-{jr}',
              'pol':row.get('POL') or 'NLRTM',
              'pod':row.get('POD') or 'NLRTM',
              'container':row.get('Container No.') or f'CLXU{jr}000',
              'bl':row.get('BL Ref') or f'CLXHBL{jr}',
              'status':row.get('Status') or 'Open',
            }
        for m in modules:
            key=m['key']; rows=extract_array_objects(text,key)
            by_job={}
            for row in rows:
                raw=json.dumps(row,sort_keys=True)
                hit=re.search(r'5000[1-5]',raw)
                if hit: by_job[hit.group(0)]=row
            recovered=[]
            for jr in jobs:
                row=dict(by_job.get(jr) or {})
                row.setdefault('Job Ref',jr)
                row.setdefault('Status','Open')
                if key=='split-bl':
                    row.setdefault('Child B/L 1',f'CLXHBL{jr}-A')
                    row.setdefault('Child B/L 2',f'CLXHBL{jr}-B')
                    row.setdefault('Container',jobs[jr]['container'])
                recovered.append(row)
            data[key]=recovered
        return {'modules':modules,'jobs':jobs,'data':data}

def run(reset=True):
    SEED=_load_seed()
    if reset and DB_PATH.exists(): DB_PATH.unlink()
    conn=connect(); conn.executescript((HERE/'schema.sql').read_text()); now='2026-09-23T20:00:00Z'; jobs=SEED['jobs']
    for jr,j in jobs.items():
        customer_code='CLX-CUS-'+jr[-3:]; conn.execute('INSERT INTO customers(code,name) VALUES(?,?)',(customer_code,j['customer'])); conn.execute('INSERT OR IGNORE INTO agents(code,name) VALUES(?,?)',(j['agent'],j['agent'])); conn.execute('INSERT OR IGNORE INTO vessels(name) VALUES(?)',(j['vessel'],)); vid=conn.execute('SELECT id FROM vessels WHERE name=?',(j['vessel'],)).fetchone()['id']; conn.execute('INSERT OR IGNORE INTO voyages(voyage_no,vessel_id) VALUES(?,?)',(j['voyage'],vid)); voyid=conn.execute('SELECT id FROM voyages WHERE voyage_no=?',(j['voyage'],)).fetchone()['id']; cid=conn.execute('SELECT id FROM customers WHERE code=?',(customer_code,)).fetchone()['id']; aid=conn.execute('SELECT id FROM agents WHERE code=?',(j['agent'],)).fetchone()['id']; conn.execute('INSERT INTO bookings(booking_ref,customer_id,agent_id,voyage_id,pol,pod) VALUES(?,?,?,?,?,?)',(j['booking'],cid,aid,voyid,j['pol'],j['pod'])); bid=conn.execute('SELECT id FROM bookings WHERE booking_ref=?',(j['booking'],)).fetchone()['id']; conn.execute('INSERT INTO jobs(job_ref,booking_id,customer_id,agent_id,voyage_id,pol,pod,operational_status) VALUES(?,?,?,?,?,?,?,?)',(jr,bid,cid,aid,voyid,j['pol'],j['pod'],j['status'])); jid=conn.execute('SELECT id FROM jobs WHERE job_ref=?',(jr,)).fetchone()['id']; crt=next((x for x in SEED['data']['crt'] if str(x.get('Job Ref'))==jr),{}); conn.execute('INSERT INTO containers(container_no,job_id,size_type) VALUES(?,?,?)',(j['container'],jid,crt.get('Size / Type','40HC'))); conn.execute('INSERT INTO bills(bill_no,job_id,kind,status) VALUES(?,?,?,?)',(j['bl'],jid,'HBL','Issued')); conn.execute('INSERT INTO bills(bill_no,job_id,kind,status) VALUES(?,?,?,?)',(f'CLXMBL{jr}',jid,'MBL','Issued' if jr in ('50001','50002','50005') else 'Draft'))
        state={'50001':('COMPLETE','SUBMITTED','CLEARED','N/A','PENDING',0,[]),'50002':('VGM MISSING','MISSING','CLEARED','N/A','BLOCKED',0,['VGM_MISSING']),'50003':('SI PENDING','PENDING','PENDING','N/A','BLOCKED',0,['DOCUMENTATION_PENDING']),'50004':('COMPLETE','SUBMITTED','CLEARED','PENDING','BLOCKED',0,['TRANSSHIPMENT_CONFIRMATION']),'50005':('COMPLETE','SUBMITTED','CLEARED','N/A','RELEASED',1,[])}[jr]
        conn.execute('INSERT INTO finance_states(job_id,payment_status,currency,outstanding,credit_hold) VALUES(?,?,?,?,?)',(jid,'CLEARED','USD',0,0)); conn.execute('INSERT INTO workflow_states(job_id,documentation_status,vgm_status,customs_status,transshipment_status,release_status,closed) VALUES(?,?,?,?,?,?,?)',(jid,*state[:6]));
        for h in state[6]: conn.execute('INSERT INTO workflow_holds(job_id,code,active,created_at) VALUES(?,?,1,?)',(jid,h,now))
        # complete normalized container timeline
        events=['EMPTY_RELEASE','PICKUP','GATE_IN','LOAD','DISCHARGE','TRANSSHIPMENT','GATE_OUT','EMPTY_RETURN','DETENTION_STORAGE','HOLD_RELEASE']
        for n,ev in enumerate(events):
            loc=j['pol'] if n<4 else (('SGSIN' if jr=='50004' and ev=='TRANSSHIPMENT' else j['pod']))
            stat='N/A' if ev=='TRANSSHIPMENT' and jr!='50004' else ('CLEAR' if ev=='HOLD_RELEASE' and jr in ('50001','50005') else 'RECORDED')
            conn.execute('INSERT INTO container_events(event_id,job_id,container_id,event_type,event_time,location,status,source_module,detail_json) VALUES(?,?,?,?,?,?,?,?,?)',(f'CE-{jr}-{n+1:02d}',jid,conn.execute('SELECT id FROM containers WHERE job_id=?',(jid,)).fetchone()['id'],ev,f'2026-10-{n+1:02d}T{8+n%10:02d}:00:00Z',loc,stat,'container-activity',json.dumps({'synthetic':True,'job_ref':jr})))
    for module,rows in SEED['data'].items():
        for i,row in enumerate(rows[:5]):
            text=json.dumps(row,sort_keys=True); m=re.search(r'5000[1-5]',text); jr=m.group(0) if m else list(jobs)[i%5]; job=conn.execute('SELECT * FROM jobs WHERE job_ref=?',(jr,)).fetchone(); container=conn.execute('SELECT id FROM containers WHERE job_id=?',(job['id'],)).fetchone()['id']; hbl=conn.execute("SELECT id FROM bills WHERE job_id=? AND kind='HBL'",(job['id'],)).fetchone()['id']; status=row.get('Status') or row.get('Release Status') or 'Open'; cur=conn.execute("INSERT INTO transaction_records(module,external_ref,job_id,booking_id,customer_id,agent_id,container_id,voyage_id,bill_id,status,version,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?)",(module,extref(module,row,i,jr),job['id'],job['booking_id'],job['customer_id'],job['agent_id'],container,job['voyage_id'],hbl,status,json.dumps(row),now,now)); seed_support(conn,module,cur.lastrowid,row,jr,job['id'],container,now)
    gl_seed_run(conn)
    treasury_seed_run(conn)
    integration_seed_run(conn)
    conn.close(); return str(DB_PATH)
if __name__=='__main__': print(run())