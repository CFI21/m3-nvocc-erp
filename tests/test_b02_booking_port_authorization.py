"""B02 Booking IAM/Port Master authorization — read-only regression guards."""
from pathlib import Path
import json
import sqlite3
import pytest
from fastapi import HTTPException
from app import main

def _conn():
    c=sqlite3.connect(':memory:')
    c.row_factory=sqlite3.Row
    c.executescript("""CREATE TABLE md_records (domain TEXT,record_key TEXT,payload_json TEXT,status TEXT);""")
    return c

@pytest.mark.parametrize("scope",["POL","POD","TS"])
def test_port_coverage_requires_active_authoritative_port(scope,monkeypatch):
    c=_conn()
    monkeypatch.setattr('app.admin._assigned_agent_offices',lambda conn,user,agent:[{'office_code':'RTM','country_code':'NL'}])
    network={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE','agent_code':'A1','office_code':'RTM','scope':scope,'port_code':'NLRTM'}
    c.execute("INSERT INTO md_records VALUES ('configuration','COV',?,'ACTIVE')",(json.dumps(network),))
    assert 'NLRTM' not in main.booking_agent_port_grants(c,1,'A1')[scope]['ports']
    c.execute("INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",(json.dumps({'unlocode':'NLRTM'}),))
    assert 'NLRTM' in main.booking_agent_port_grants(c,1,'A1')[scope]['ports']
    c.execute("UPDATE md_records SET status='INACTIVE' WHERE domain='port'")
    assert 'NLRTM' not in main.booking_agent_port_grants(c,1,'A1')[scope]['ports']
    c.close()

def test_mismatched_port_and_iam_country_deny(monkeypatch):
    c=_conn()
    monkeypatch.setattr('app.admin._assigned_agent_offices',lambda conn,user,agent:[{'office_code':'RTM','country_code':'DE'}])
    network={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE','agent_code':'A1','office_code':'RTM','scope':'POL','port_code':'NLRTM'}
    c.execute("INSERT INTO md_records VALUES ('configuration','COV',?,'ACTIVE')",(json.dumps(network),))
    c.execute("INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",(json.dumps({'unlocode':'NLRTM'}),))
    assert 'NLRTM' not in main.booking_agent_port_grants(c,1,'A1')['POL']['ports']
    c.close()

def test_mismatched_port_master_unlocode_deny(monkeypatch):
    c=_conn()
    monkeypatch.setattr('app.admin._assigned_agent_offices',lambda conn,user,agent:[{'office_code':'RTM','country_code':'NL'}])
    network={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE','agent_code':'A1','office_code':'RTM','scope':'POL','port_code':'NLRTM'}
    c.execute("INSERT INTO md_records VALUES ('configuration','COV',?,'ACTIVE')",(json.dumps(network),))
    c.execute("INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",(json.dumps({'unlocode':'NLAMS'}),))
    assert not main.booking_agent_port_grants(c,1,'A1')['POL']['ports']
    c.close()


@pytest.mark.parametrize("office_code,agent_code", [
    ("rtm", "a1"),
    (" RTM ", " A1 "),
    ("RTM", "A1"),
])
def test_booking_port_coverage_normalizes_existing_master_identifiers(
    monkeypatch, office_code, agent_code
):
    c=_conn()
    monkeypatch.setattr(
        'app.admin._assigned_agent_offices',
        lambda conn,user,agent:[{'office_code':'RTM','country_code':'NL'}],
    )
    network={
        'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE',
        'agent_code':agent_code,'office_code':office_code,
        'scope':'POL','port_code':'NLRTM',
    }
    c.execute(
        "INSERT INTO md_records VALUES ('configuration','COV',?,'ACTIVE')",
        (json.dumps(network),),
    )
    c.execute(
        "INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",
        (json.dumps({'unlocode':'NLRTM'}),),
    )
    try:
        assert main.booking_agent_port_grants(c,1,'A1')['POL']['ports']=={'NLRTM'}
    finally:
        c.close()


def test_booking_port_coverage_rejects_unassigned_office(monkeypatch):
    c=_conn()
    monkeypatch.setattr(
        'app.admin._assigned_agent_offices',
        lambda conn,user,agent:[{'office_code':'RTM','country_code':'NL'}],
    )
    network={
        'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE',
        'agent_code':'A1','office_code':'AMS','scope':'POL','port_code':'NLRTM',
    }
    c.execute(
        "INSERT INTO md_records VALUES ('configuration','COV',?,'ACTIVE')",
        (json.dumps(network),),
    )
    c.execute(
        "INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",
        (json.dumps({'unlocode':'NLRTM'}),),
    )
    try:
        assert not main.booking_agent_port_grants(c,1,'A1')['POL']['ports']
    finally:
        c.close()
