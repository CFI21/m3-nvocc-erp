from app import db
from app.seed import run as seed_run
from app.control_tower import _job_rows
from app.management_kpi import _snapshot_payload, control_status

def test_control_tower_excludes_controlled_uat_job(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'uat-seg.db')
    seed_run(True)
    c=db.connect()
    customer_id=c.execute("select id from customers order by id limit 1").fetchone()['id']
    agent_id=c.execute("select id from agents order by id limit 1").fetchone()['id']
    voyage_id=c.execute("select id from voyages order by id limit 1").fetchone()['id']
    c.execute("insert into bookings(booking_ref,customer_id,agent_id,voyage_id,pol,pod) values('UAT-SEG-92200',?,?,?,?,?)",
              (customer_id,agent_id,voyage_id,'SGSIN','NLRTM'))
    booking_id=c.execute("select id from bookings where booking_ref='UAT-SEG-92200'").fetchone()['id']
    c.execute("insert into jobs(job_ref,booking_id,customer_id,agent_id,voyage_id,pol,pod,operational_status,version) values('92200',?,?,?,?,?,?,'OPEN',1)",
              (booking_id,customer_id,agent_id,voyage_id,'SGSIN','NLRTM'))
    job_id=c.execute("select id from jobs where job_ref='92200'").fetchone()['id']
    c.execute("insert into workflow_states(job_id,documentation_status,vgm_status,customs_status,transshipment_status,release_status,closed,version) values(?,'Draft','Pending','Pending','N/A','Pending',0,1)",(job_id,))
    c.execute("insert into finance_states(job_id,payment_status,currency,outstanding,credit_hold) values(?,'Open','USD',999999,1)",(job_id,))
    rows=_job_rows(c,'ADMIN',None,None)
    assert all(x['job_ref']!='92200' for x in rows)
    assert len(rows)==5
    c.close()

def test_management_snapshot_ignores_uat_financial_exposure(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'uat-kpi.db')
    seed_run(True)
    c=db.connect()
    c.executescript(open('migrations/CLX025_001_operations_workbench.sql').read())
    baseline=_snapshot_payload(c,'ADMIN')
    customer_id=c.execute("select id from customers order by id limit 1").fetchone()['id']
    agent_id=c.execute("select id from agents order by id limit 1").fetchone()['id']
    voyage_id=c.execute("select id from voyages order by id limit 1").fetchone()['id']
    c.execute("insert into bookings(booking_ref,customer_id,agent_id,voyage_id,pol,pod) values('UAT-KPI-92200',?,?,?,?,?)",
              (customer_id,agent_id,voyage_id,'SGSIN','NLRTM'))
    booking_id=c.execute("select id from bookings where booking_ref='UAT-KPI-92200'").fetchone()['id']
    c.execute("insert into jobs(job_ref,booking_id,customer_id,agent_id,voyage_id,pol,pod,operational_status,version) values('92200',?,?,?,?,?,?,'OPEN',1)",
              (booking_id,customer_id,agent_id,voyage_id,'SGSIN','NLRTM'))
    job_id=c.execute("select id from jobs where job_ref='92200'").fetchone()['id']
    c.execute("insert into workflow_states(job_id,documentation_status,vgm_status,customs_status,transshipment_status,release_status,closed,version) values(?,'Draft','Pending','Pending','N/A','Pending',0,1)",(job_id,))
    c.execute("insert into finance_states(job_id,payment_status,currency,outstanding,credit_hold) values(?,'Open','USD',999999,1)",(job_id,))
    after=_snapshot_payload(c,'ADMIN')
    assert after['summary']==baseline['summary']
    c.close()

def test_management_control_reports_196_screen_contract():
    assert control_status('AUDITOR')['screen_catalog_preserved']==196
