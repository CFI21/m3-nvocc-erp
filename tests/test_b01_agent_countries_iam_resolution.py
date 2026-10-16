"""B01 country derivation: isolated SQLite regressions, no Neon writes."""
import json
import sqlite3
import pytest
from fastapi import HTTPException
from app import masterdata


@pytest.fixture
def country_db(tmp_path, monkeypatch):
    filename = str(tmp_path / "b01_country.sqlite3")
    def connection():
        c = sqlite3.connect(filename, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE IF NOT EXISTS iam_countries (id INTEGER PRIMARY KEY, country_code TEXT, name TEXT, status TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS iam_offices (id INTEGER PRIMARY KEY, office_code TEXT, country_id INTEGER, status TEXT)")
        masterdata.init_schema(c)
        return c
    monkeypatch.setattr(masterdata, "connect", connection)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: False)
    monkeypatch.setattr(masterdata, "authenticated_actor", lambda c, token: ({"user_ref": "UAT-B01-MAKER"}, {"MASTER_DATA"}))
    c = connection()
    c.execute("INSERT INTO iam_countries VALUES (1,'NL','Netherlands','ACTIVE')")
    c.execute("INSERT INTO iam_offices VALUES (1,'RTM',1,'ACTIVE')")
    c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) VALUES ('agent','UAT-AGENT-001','{}','ACTIVE','now','now')")
    c.commit()
    c.close()
    return connection


def assign(connect, key="AGCFG-1", agent="UAT-AGENT-001", office="RTM", status="ACTIVE", kind="AGENT_OFFICE_ASSIGNMENT"):
    c = connect()
    p = {"config_type": kind, "agent_code": agent, "office_code": office}
    c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) VALUES ('configuration',?,?,?,'now','now')", (key,json.dumps(p),status))
    c.commit()
    c.close()


def test_approved_rtm_resolves_nl_without_country_copy(country_db):
    assign(country_db)
    result = masterdata.agent_office_countries("valid")
    assert result == [{"agent_code":"UAT-AGENT-001","office_code":"RTM","source":"OFFICE","country_code":"NL","country_name":"Netherlands"}]
    c = country_db()
    p = json.loads(c.execute("SELECT payload_json FROM md_records WHERE record_key='AGCFG-1'").fetchone()[0])
    assert "country_code" not in p
    c.close()


def test_duplicate_active_assignments_deduplicate(country_db):
    assign(country_db)
    assign(country_db,key="AGCFG-2")
    assert len(masterdata.agent_office_countries("valid")) == 1


@pytest.mark.parametrize("condition", ["pending","inactive_office","inactive_country","inactive_agent","unknown_office"])
def test_fail_closed(country_db, condition):
    if condition == "pending":
        assign(country_db,status="INACTIVE")
    else:
        assign(country_db,office="UNKNOWN" if condition == "unknown_office" else "RTM")
        c = country_db()
        if condition == "inactive_office":
            c.execute("UPDATE iam_offices SET status='INACTIVE' WHERE office_code='RTM'")
        if condition == "inactive_country":
            c.execute("UPDATE iam_countries SET status='INACTIVE' WHERE country_code='NL'")
        if condition == "inactive_agent":
            c.execute("UPDATE md_records SET status='INACTIVE' WHERE domain='agent'")
        c.commit()
        c.close()
    assert masterdata.agent_office_countries("valid") == []


def test_no_iam_permission_changes(country_db):
    assign(country_db)
    c = country_db()
    c.execute("CREATE TABLE iam_user_roles(id INTEGER PRIMARY KEY, user_id INTEGER, office_id INTEGER)")
    c.execute("INSERT INTO iam_user_roles VALUES (1,5,1)")
    c.commit()
    before = c.execute("SELECT * FROM iam_user_roles").fetchall()
    c.close()
    masterdata.agent_office_countries("valid")
    c = country_db()
    after = c.execute("SELECT * FROM iam_user_roles").fetchall()
    assert [tuple(x) for x in before] == [tuple(x) for x in after]
    c.close()


def test_reject_non_master_role(country_db, monkeypatch):
    monkeypatch.setattr(masterdata, "authenticated_actor", lambda c, token: ({}, {"VIEWER"}))
    with pytest.raises(HTTPException) as exc:
        masterdata.agent_office_countries("valid")
    assert exc.value.status_code == 403


def test_approved_pol_coverage_resolves_country_without_new_iam_grant(country_db):
    c=country_db()
    c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) VALUES ('port','NLRTM',?,'ACTIVE','now','now')",(json.dumps({'unlocode':'NLRTM'}),))
    coverage={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE','scope':'POL','agent_code':'UAT-AGENT-001','office_code':'RTM','port_code':'NLRTM'}
    c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) VALUES ('configuration','COV-1',?,'ACTIVE','now','now')",(json.dumps(coverage),))
    c.commit();c.close()
    result=masterdata.agent_office_countries('valid')
    assert result==[{'agent_code':'UAT-AGENT-001','office_code':'RTM','port_code':'NLRTM','source':'POL','record_key':'COV-1','country_code':'NL','country_name':'Netherlands'}]


def test_pending_or_inactive_pol_never_projects(country_db):
    c=country_db()
    coverage={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE','scope':'POL','agent_code':'UAT-AGENT-001','office_code':'RTM','port_code':'NLRTM'}
    c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) VALUES ('configuration','COV-2',?,'INACTIVE','now','now')",(json.dumps(coverage),))
    c.commit();c.close()
    assert masterdata.agent_office_countries('valid')==[]


def test_pol_requires_approved_port_and_active_country(country_db):
    c=country_db()
    coverage={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE','scope':'POL','agent_code':'UAT-AGENT-001','office_code':'RTM','port_code':'NLRTM'}
    assert 'AGENT_NETWORK_PORT_NOT_ACTIVE' in masterdata.reference_errors(c,'configuration',coverage)
    c.close()


def test_country_actions_keep_existing_maker_checker_endpoint():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    ui=(root/'web'/'index.html').read_text()
    assert 'data-country-edit' in ui and 'data-country-remove' in ui
    assert 'id="m3CountryForm"' in ui
    assert "m3CountryChange(key?'UPDATE':'CREATE'" in ui
    assert "'/api/masterdata/changes'" in ui
    assert 'm3AgentIdentity?.username' in ui


def test_pol_cross_country_office_is_denied(country_db):
    c=country_db()
    coverage={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE','scope':'POL','agent_code':'UAT-AGENT-001','office_code':'RTM','port_code':'DEHAM'}
    errors=masterdata.reference_errors(c,'configuration',coverage)
    assert 'AGENT_NETWORK_OFFICE_COUNTRY_MISMATCH' in errors
    c.close()


def test_countries_tab_preserves_six_tabs():
    from pathlib import Path
    ui=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    assert "['Agent Detail','Emails','Countries','Offices','CRO Detail','B/L Template']" in ui
    assert "m3CountryChange('DEACTIVATE'" in ui


def test_pol_update_duplicate_guard_is_present():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'app'/'masterdata.py').read_text()
    assert "b.domain=='configuration' and b.operation=='UPDATE'" in source
    assert "candidate={**existing_payload,**b.payload}" in source
    assert "DUPLICATE_AGENT_RELATIONSHIP" in source


def test_checker_revalidates_pol_uniqueness():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'app'/'masterdata.py').read_text()
    decision=source.split('def decide(change_ref:',1)[1]
    assert "ch['domain']=='configuration' and ch['operation'] in ('CREATE','UPDATE')" in decision
    assert "DUPLICATE_AGENT_RELATIONSHIP" in decision


def test_pol_projection_fails_closed_if_port_unlocode_changes(country_db):
    c=country_db()
    coverage={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE','scope':'POL','agent_code':'UAT-AGENT-001','office_code':'RTM','port_code':'NLRTM'}
    c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) VALUES ('port','NLRTM',?,'ACTIVE','now','now')",(json.dumps({'unlocode':'DEHAM'}),))
    c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) VALUES ('configuration','COV-X',?,'ACTIVE','now','now')",(json.dumps(coverage),))
    c.commit();c.close()
    assert masterdata.agent_office_countries('valid') == []


def test_pol_projection_fails_closed_if_port_inactive(country_db):
    c=country_db()
    coverage={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE','scope':'POL','agent_code':'UAT-AGENT-001','office_code':'RTM','port_code':'NLRTM'}
    c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) VALUES ('port','NLRTM',?,'INACTIVE','now','now')",(json.dumps({'unlocode':'NLRTM'}),))
    c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) VALUES ('configuration','COV-X',?,'ACTIVE','now','now')",(json.dumps(coverage),))
    c.commit();c.close()
    assert masterdata.agent_office_countries('valid') == []


def test_removal_request_appears_in_independent_checker_queue():
    from pathlib import Path
    ui=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
    assert "x.payload?.agent_code||approved?.payload?.agent_code" in ui
    assert "m3AgentConfigs.find(c=>c.record_key===x.record_key&&c.status==='ACTIVE')" in ui
    assert "data-agent-related-decision" in ui


def test_governed_pol_create_and_remove_with_independent_checker(country_db, monkeypatch):
    c=country_db()
    c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) VALUES ('port','NLRTM',?,'ACTIVE','now','now')", (json.dumps({'unlocode':'NLRTM'}),))
    c.execute('CREATE TABLE iam_user_roles (id INTEGER PRIMARY KEY,user_id INTEGER,office_id INTEGER)')
    c.execute('INSERT INTO iam_user_roles VALUES (1,7,1)')
    c.commit()
    c.close()

    def identity(_c, token):
        if token=='maker':
            return {'user_ref':'maker-ref','username':'md.maker'}, {'MASTER_DATA'}
        if token=='checker':
            return {'user_ref':'checker-ref','username':'md.checker'}, {'MASTER_DATA_MANAGER'}
        raise HTTPException(401, 'Invalid test identity')
    monkeypatch.setattr(masterdata,'authenticated_actor',identity)

    coverage={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE',
              'scope':'POL','agent_code':'UAT-AGENT-001',
              'office_code':'RTM','port_code':'NLRTM'}
    created=masterdata.create_change(
        masterdata.Change(domain='configuration',record_key='COV-LIFECYCLE',
                          operation='CREATE',payload=coverage,reason='UAT coverage'), 'maker')
    with pytest.raises(HTTPException) as denied:
        masterdata.decide(created['change_ref'],
                          masterdata.Decision(decision='APPROVE',comment='Self approval'),
                          'maker')
    assert denied.value.status_code == 403
    assert masterdata.agent_office_countries('checker') == []

    approved=masterdata.decide(
        created['change_ref'],masterdata.Decision(decision='APPROVE',comment='Reviewed'), 'checker')
    assert approved['status']=='APPROVED'
    projected=masterdata.agent_office_countries('checker')
    assert any(x.get('source')=='POL' and x.get('port_code')=='NLRTM' for x in projected)

    removed=masterdata.create_change(
        masterdata.Change(domain='configuration',record_key='COV-LIFECYCLE',
                          operation='DEACTIVATE',payload={},reason='End coverage'), 'maker')
    assert any(x.get('source')=='POL' for x in masterdata.agent_office_countries('checker'))
    result=masterdata.decide(
        removed['change_ref'],masterdata.Decision(decision='APPROVE',comment='Reviewed removal'),
        'checker')
    assert result['status']=='APPROVED'
    assert not any(x.get('source')=='POL' for x in masterdata.agent_office_countries('checker'))
    c=country_db()
    assert [tuple(x) for x in c.execute('SELECT * FROM iam_user_roles')]==[(1,7,1)]
    c.close()


def test_governed_pol_edit_requires_second_identity(country_db, monkeypatch):
    c=country_db()
    for code in ('NLRTM','NLAMS'):
        c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,created_at,updated_at) VALUES ('port',?,?,'ACTIVE','now','now')",
                  (code,json.dumps({'unlocode':code})))
    old={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE',
         'scope':'POL','agent_code':'UAT-AGENT-001','office_code':'RTM','port_code':'NLRTM'}
    c.execute("INSERT INTO md_records(domain,record_key,payload_json,status,version,created_at,updated_at) VALUES ('configuration','POL-EDIT',?,'ACTIVE',1,'now','now')",
              (json.dumps(old),))
    c.commit();c.close()

    def identity(_c, token):
        if token=='maker': return {'user_ref':'maker-ref','username':'md.maker'}, {'MASTER_DATA'}
        if token=='checker': return {'user_ref':'checker-ref','username':'md.checker'}, {'MASTER_DATA_MANAGER'}
        raise HTTPException(401,'Invalid identity')
    monkeypatch.setattr(masterdata,'authenticated_actor',identity)

    submitted=masterdata.create_change(
        masterdata.Change(domain='configuration',record_key='POL-EDIT',
                          operation='UPDATE',payload={'port_code':'NLAMS'},
                          reason='Change POL'), 'maker')
    assert submitted['status']=='PENDING'
    before=masterdata.agent_office_countries('checker')
    assert any(x.get('port_code')=='NLRTM' for x in before)
    assert not any(x.get('port_code')=='NLAMS' for x in before)
    with pytest.raises(HTTPException) as denied:
        masterdata.decide(submitted['change_ref'],masterdata.Decision(decision='APPROVE',comment='Self'), 'maker')
    assert denied.value.status_code==403
    accepted=masterdata.decide(submitted['change_ref'],masterdata.Decision(decision='APPROVE',comment='Checked'), 'checker')
    assert accepted['status']=='APPROVED'
    after=masterdata.agent_office_countries('checker')
    assert any(x.get('port_code')=='NLAMS' for x in after)
    assert not any(x.get('port_code')=='NLRTM' for x in after)
