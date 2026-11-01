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


@pytest.mark.parametrize("scope", [" pol ", "Pod", " TS "])
def test_booking_agent_network_scope_whitespace_does_not_disable_authorized_port(monkeypatch, scope):
    c=_conn()
    monkeypatch.setattr(
        'app.admin._assigned_agent_offices',
        lambda conn,user,agent:[{'office_code':'RTM','country_code':'NL'}],
    )
    record={
        'config_type':'AGENT_NETWORK',
        'network_type':'PORT_COVERAGE',
        'agent_code':'A1',
        'office_code':'RTM',
        'scope':scope,
        'port_code':'NLRTM',
    }
    c.execute(
        "INSERT INTO md_records VALUES ('configuration','SCOPE',?,'ACTIVE')",
        (json.dumps(record),),
    )
    c.execute(
        "INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",
        (json.dumps({'unlocode':'NLRTM'}),),
    )
    try:
        normalized=scope.strip().upper()
        grants=main.booking_agent_port_grants(c,1,'A1')
        assert grants[normalized]['ports']=={'NLRTM'}
        assert all(not value['ports'] for key,value in grants.items() if key!=normalized)
    finally:
        c.close()


@pytest.mark.parametrize("configuration_type,network_type", [
    (" agent_network ", " PORT_COVERAGE "),
    ("Agent_Network", "Port_Coverage"),
])
def test_booking_coverage_config_type_normalization(monkeypatch, configuration_type, network_type):
    c=_conn()
    monkeypatch.setattr(
        'app.admin._assigned_agent_offices',
        lambda conn,user,agent:[{'office_code':'RTM','country_code':'NL'}],
    )
    record={
        'config_type':configuration_type,
        'network_type':network_type,
        'agent_code':'A1','office_code':'RTM',
        'scope':'POL','port_code':'NLRTM',
    }
    c.execute(
        "INSERT INTO md_records VALUES ('configuration','TYPE',?,'ACTIVE')",
        (json.dumps(record),),
    )
    c.execute(
        "INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",
        (json.dumps({'unlocode':'NLRTM'}),),
    )
    try:
        assert main.booking_agent_port_grants(c,1,'A1')['POL']['ports']=={'NLRTM'}
    finally:
        c.close()


@pytest.mark.parametrize("payload", ["[]", "null", '"bad"', "42", "{malformed"])
def test_booking_ignores_invalid_agent_network_payload(monkeypatch, payload):
    c=_conn()
    monkeypatch.setattr('app.admin._assigned_agent_offices',
                        lambda conn,user,agent:[{'office_code':'RTM','country_code':'NL'}])
    c.execute("INSERT INTO md_records VALUES ('configuration','INVALID',?,'ACTIVE')",(payload,))
    try:
        grants=main.booking_agent_port_grants(c,1,'A1')
        assert all(not entry['ports'] for entry in grants.values())
    finally:
        c.close()


@pytest.mark.parametrize("payload", ["[]", "null", '"bad"', "42", "{malformed"])
def test_booking_ignores_invalid_port_master_payload(monkeypatch, payload):
    c=_conn()
    monkeypatch.setattr('app.admin._assigned_agent_offices',
                        lambda conn,user,agent:[{'office_code':'RTM','country_code':'NL'}])
    cfg={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE',
         'agent_code':'A1','office_code':'RTM','scope':'POL','port_code':'NLRTM'}
    c.execute("INSERT INTO md_records VALUES ('configuration','COV',?,'ACTIVE')",(json.dumps(cfg),))
    c.execute("INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",(payload,))
    try:
        assert not main.booking_agent_port_grants(c,1,'A1')['POL']['ports']
    finally:
        c.close()


@pytest.mark.parametrize("assignments", [None, [{}], [{'office_code':'RTM','country_code':'BAD'}],
                                            [{'office_code':None,'country_code':'NL'}]])
def test_booking_invalid_iam_office_assignment_denied(monkeypatch, assignments):
    c=_conn()
    monkeypatch.setattr('app.admin._assigned_agent_offices',
                        lambda conn,user,agent:assignments)
    try:
        assert all(not item['ports'] for item in main.booking_agent_port_grants(c,1,'A1').values())
    finally:
        c.close()


@pytest.mark.parametrize("countries", [
    ("NL", "DE"), ("DE", "NL"), ("NL", "DE", "NL"),
])
def test_booking_denies_conflicting_iam_office_country_grants(monkeypatch, countries):
    c=_conn()
    assignments=[{'office_code':'RTM','country_code':country} for country in countries]
    monkeypatch.setattr('app.admin._assigned_agent_offices',
                        lambda conn,user,agent:assignments)
    record={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE',
            'agent_code':'A1','office_code':'RTM','scope':'POL','port_code':'NLRTM'}
    c.execute("INSERT INTO md_records VALUES ('configuration','COV',?,'ACTIVE')",(json.dumps(record),))
    c.execute("INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",
              (json.dumps({'unlocode':'NLRTM'}),))
    try:
        assert main.booking_agent_port_grants(c,1,'A1')['POL']['ports']==set()
    finally:
        c.close()


def test_booking_accepts_duplicate_same_country_iam_office_grants(monkeypatch):
    c=_conn()
    monkeypatch.setattr('app.admin._assigned_agent_offices',
                        lambda conn,user,agent:[
                            {'office_code':'RTM','country_code':'NL'},
                            {'office_code':'rtm','country_code':'nl'},
                        ])
    record={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE',
            'agent_code':'A1','office_code':'RTM','scope':'POL','port_code':'NLRTM'}
    c.execute("INSERT INTO md_records VALUES ('configuration','COV',?,'ACTIVE')",(json.dumps(record),))
    c.execute("INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",
              (json.dumps({'unlocode':'NLRTM'}),))
    try:
        assert main.booking_agent_port_grants(c,1,'A1')['POL']['ports']=={'NLRTM'}
    finally:
        c.close()


@pytest.mark.parametrize("countries", [
    ("NL", "DE", "NL"),
    ("DE", "NL", "DE"),
    ("NL", "DE", "NL", "NL"),
])
def test_booking_conflicted_office_never_regains_access(monkeypatch, countries):
    c=_conn()
    monkeypatch.setattr('app.admin._assigned_agent_offices',
                        lambda conn,user,agent:[
                            {'office_code':'RTM','country_code':country}
                            for country in countries
                        ])
    network={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE',
             'agent_code':'A1','office_code':'RTM','scope':'POL','port_code':'NLRTM'}
    c.execute("INSERT INTO md_records VALUES ('configuration','COV',?,'ACTIVE')",
              (json.dumps(network),))
    c.execute("INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",
              (json.dumps({'unlocode':'NLRTM'}),))
    try:
        assert main.booking_agent_port_grants(c,1,'A1')['POL']['ports']==set()
    finally:
        c.close()


@pytest.mark.parametrize("start,end", [
    ("2026-99-01", "9999-12-31"),
    ("not-a-date", "9999-12-31"),
    ("2026-02-30", "9999-12-31"),
    ("0000-01-01", "tomorrow"),
    ("2026-10-20", "2026-10-19"),
])
def test_booking_rejects_invalid_agent_network_effective_period(monkeypatch,start,end):
    c=_conn()
    monkeypatch.setattr('app.admin._assigned_agent_offices',
                        lambda conn,user,agent:[{'office_code':'RTM','country_code':'NL'}])
    data={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE',
          'agent_code':'A1','office_code':'RTM','scope':'POL',
          'port_code':'NLRTM','effective_from':start,'effective_to':end}
    c.execute("INSERT INTO md_records VALUES ('configuration','COV',?,'ACTIVE')",
              (json.dumps(data),))
    c.execute("INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",
              (json.dumps({'unlocode':'NLRTM'}),))
    try:
        assert main.booking_agent_port_grants(c,1,'A1')['POL']['ports']==set()
    finally:
        c.close()


@pytest.mark.parametrize("offset_start,offset_end,expected", [
    (-1, 1, True), (-3, -1, False), (1, 3, False),
    (0, 0, True), (1, -1, False),
])
def test_booking_agent_network_effective_window_boundaries(
    monkeypatch, offset_start, offset_end, expected
):
    from datetime import datetime, timezone, timedelta
    c=_conn()
    monkeypatch.setattr(
        'app.admin._assigned_agent_offices',
        lambda conn,user,agent:[{'office_code':'RTM','country_code':'NL'}],
    )
    today=datetime.now(timezone.utc).date()
    data={'config_type':'AGENT_NETWORK','network_type':'PORT_COVERAGE',
          'agent_code':'A1','office_code':'RTM','scope':'POL',
          'port_code':'NLRTM',
          'effective_from':(today+timedelta(days=offset_start)).isoformat(),
          'effective_to':(today+timedelta(days=offset_end)).isoformat()}
    c.execute("INSERT INTO md_records VALUES ('configuration','COV',?,'ACTIVE')",
              (json.dumps(data),))
    c.execute("INSERT INTO md_records VALUES ('port','NLRTM',?,'ACTIVE')",
              (json.dumps({'unlocode':'NLRTM'}),))
    try:
        ports=main.booking_agent_port_grants(c,1,'A1')['POL']['ports']
        assert ('NLRTM' in ports) is expected
    finally:
        c.close()


@pytest.mark.parametrize("fields", [None, [], "POL=NLRTM", 42, True])
def test_booking_rejects_non_mapping_payload_before_port_authorization(fields):
    grants={
        'POL':{'ports':{'NLRTM'},'all_ports':False},
        'POD':{'ports':set(),'all_ports':False},
        'TS':{'ports':set(),'all_ports':False},
    }
    with pytest.raises(HTTPException) as exc:
        main.booking_validate_authorized_ports(fields,grants)
    assert exc.value.status_code==422
    assert exc.value.detail['code']=='BOOKING_FIELDS_INVALID'


def test_booking_port_grant_cannot_be_supplied_as_plain_string():
    with pytest.raises(HTTPException) as exc:
        main.booking_validate_authorized_ports({'POL':'NLRTM'}, {'POL':'NLRTM'})
    assert exc.value.status_code==403
    assert exc.value.detail['code']=='BOOKING_PORT_NOT_AUTHORIZED'


@pytest.mark.parametrize("value", [[], {}, 42, True, " ", "\t"])
def test_booking_rejects_invalid_populated_port_fields(value):
    grants={
        scope:{'ports':{'NLRTM'},'all_ports':False}
        for scope in ('POL','POD','TS')
    }
    for field in ('POL','POD','POT (1)','POT (2)'):
        with pytest.raises(HTTPException) as exc:
            main.booking_validate_authorized_ports({field:value},grants)
        assert exc.value.status_code==422
        assert exc.value.detail=={'code':'BOOKING_PORT_INVALID','field':field}


def test_booking_authorized_port_is_accepted_with_explicit_scope_grant():
    grants={'POL':{'ports':{'NLRTM'},'all_ports':False}}
    assert main.booking_validate_authorized_ports({'POL':'NLRTM'},grants) is True


@pytest.mark.parametrize("port", ["XX", "NLRTM1", "12345", "N!RTM", "NL RTM", "NLRT", "NLRTMM", " NL R TM "])
def test_booking_rejects_invalid_unlocode_format_before_grant_check(port):
    for field,scope in (("POL","POL"),("POD","POD"),("POT (1)","TS"),("POT (2)","TS")):
        grants={scope:{"ports":{port},"all_ports":False}}
        with pytest.raises(HTTPException) as exc:
            main.booking_validate_authorized_ports({field:port},grants)
        assert exc.value.status_code==422
        assert exc.value.detail=={"code":"BOOKING_PORT_INVALID","field":field}


def test_booking_valid_unlocode_format_requires_actual_grant():
    with pytest.raises(HTTPException) as exc:
        main.booking_validate_authorized_ports({"POL":"NLRTM"}, {"POL":{"ports":set(),"all_ports":False}})
    assert exc.value.status_code==403
    assert exc.value.detail["code"]=="BOOKING_PORT_NOT_AUTHORIZED"


@pytest.mark.parametrize("port", ["nlrtm", " NLRTM ", "NlRtM"])
@pytest.mark.parametrize("field,scope", [
    ("POL", "POL"), ("POD", "POD"), ("POT (1)", "TS"), ("POT (2)", "TS")
])
def test_booking_canonical_unlocode_matches_verified_iam_grant(port, field, scope):
    grants={scope:{"ports":{"NLRTM"},"all_ports":False}}
    assert main.booking_validate_authorized_ports({field:port},grants) is True


@pytest.mark.parametrize("field,scope", [
    ("POL", "POL"), ("POD", "POD"), ("POT (1)", "TS"), ("POT (2)", "TS")
])
def test_booking_canonical_unlocode_still_denies_missing_grant(field,scope):
    grants={scope:{"ports":{"DEHAM"},"all_ports":False}}
    with pytest.raises(HTTPException) as exc:
        main.booking_validate_authorized_ports({field:" nlrtm "},grants)
    assert exc.value.status_code==403
    assert exc.value.detail["code"]=="BOOKING_PORT_NOT_AUTHORIZED"


@pytest.mark.parametrize("agent_code", [None, 17, True, [], {}, "", " ", "A 1", "A\t1", "A\n1"])
def test_booking_agent_iam_grant_rejects_invalid_party_identifiers(agent_code):
    class NoSql:
        def execute(self, *_args, **_kwargs):
            pytest.fail("Invalid IAM agent key must be rejected before SQL")
    assert main.booking_iam_agent_granted(NoSql(), 7, agent_code) is False


def test_booking_agent_iam_grant_uses_parameterized_exact_party_key():
    class ExactSql:
        def execute(self, sql, args):
            assert "party_type='AGENT'" in sql
            assert "status='ACTIVE'" in sql
            assert args == (7, "A1")
            return self
        def fetchone(self):
            return (1,)
    assert main.booking_iam_agent_granted(ExactSql(), 7, " A1 ") is True


@pytest.mark.parametrize("port", ["12345", "NLRT", "NLRTMM", "N!RTM", "NL RTM", "", None])
def test_booking_direct_port_grant_rejects_malformed_unlocode_even_when_global(port):
    assert main.booking_port_allowed(port, {"NLRTM"}, all_ports_granted=True) is False


@pytest.mark.parametrize("port", ["nlrtm", " NLRTM ", "NlRtM"])
def test_booking_direct_port_grant_accepts_canonical_valid_unlocode(port):
    assert main.booking_port_allowed(port, {"NLRTM"}) is True


@pytest.mark.parametrize("port", [12345, True, False, ["NLRTM"], {"code":"NLRTM"}, 0.0])
def test_booking_direct_port_grant_denies_nonstring_values_before_global_permission(port):
    assert main.booking_port_allowed(port, {"NLRTM"}, all_ports_granted=True) is False
    assert main.booking_port_allowed(port, {"NLRTM"}, all_ports_granted=False) is False


@pytest.mark.parametrize("allowlist", [
    [" NLRTM ", None, 123, "BAD", "DEHAM"],
    ("nlrtm", "NLRTM1"),
    {"NLRTM", "", "NL RTM"},
])
def test_booking_allowlist_filters_invalid_entries_without_revoking_valid_port(allowlist):
    assert main.booking_port_allowed("NLRTM",allowlist) is True


@pytest.mark.parametrize("allowlist", [
    ["NLRTM1", "12345", None, 17],
    {"BAD", "NL RTM", ""},
    ["NLRTM"],
])
def test_booking_allowlist_never_grants_unlisted_port(allowlist):
    assert main.booking_port_allowed("DEHAM",allowlist) is False


@pytest.mark.parametrize("user_id", [0, -1, -99, True, False, "7", None, 7.0])
def test_booking_iam_party_grant_rejects_invalid_user_ids_before_sql(user_id):
    class NoSql:
        def execute(self, *args, **kwargs):
            pytest.fail("Invalid user ID must not reach IAM SQL")
    assert main.booking_iam_agent_granted(NoSql(), user_id, "A1") is False


@pytest.mark.parametrize("user_id,agent_code", [
    (0,"A1"),(-1,"A1"),(True,"A1"),("7","A1"),
    (7,None),(7,123),(7,""),(7,"  "),
])
def test_booking_port_grants_invalid_identity_skips_iam_lookup(monkeypatch,user_id,agent_code):
    monkeypatch.setattr(
        'app.admin._assigned_agent_offices',
        lambda *_args: pytest.fail("Invalid IAM actor queried assignments"),
    )
    grants=main.booking_agent_port_grants(None,user_id,agent_code)
    assert set(grants)=={'POL','POD','TS'}
    assert all(not rule['ports'] and rule['all_ports'] is False for rule in grants.values())
