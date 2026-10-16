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
        c = sqlite3.connect(filename)
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
    assert result == [{"agent_code":"UAT-AGENT-001","office_code":"RTM","country_code":"NL","country_name":"Netherlands"}]
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
