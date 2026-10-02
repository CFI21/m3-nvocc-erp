import json
from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.masterdata import sync_operational_projection

def test_agent_master_syncs_to_existing_operational_projection(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'agent_projection.db')
    seed_run(True); admin_seed_run(); masterdata_seed_run()
    c=db.connect()
    payload={'name':'ANC WORLDWIDE CONTAINERS LINE (M) SDN BHD','common_party_key':'ANCML','country':'MY','port':'MYPKG'}
    sync_operational_projection(c,'agent','220',payload,'ACTIVE')
    row=c.execute("SELECT code,name FROM agents WHERE code='220'").fetchone()
    assert row['code']=='220'
    assert row['name']==payload['name']
    before=c.execute("SELECT COUNT(*) n FROM agents WHERE code='220'").fetchone()['n']
    sync_operational_projection(c,'agent','220',{**payload,'name':'ANCML Updated'},'ACTIVE')
    after=c.execute("SELECT COUNT(*) n FROM agents WHERE code='220'").fetchone()['n']
    assert before==1 and after==1
    assert c.execute("SELECT name FROM agents WHERE code='220'").fetchone()['name']=='ANCML Updated'
    c.close()

def test_projection_sync_does_not_create_new_master_model():
    from pathlib import Path
    schema=Path('app/schema.sql').read_text().lower()
    assert 'create table if not exists agent_projection' not in schema
    assert 'create table if not exists customer_agent' not in schema
