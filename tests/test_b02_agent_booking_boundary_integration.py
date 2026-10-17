"""B02 cross-agent authorization integration: IAM office -> Agent Network -> Port Master -> Booking."""
import json
import sqlite3
import pytest
from fastapi import HTTPException
from app import main


def _db():
    conn=sqlite3.connect(":memory:")
    conn.row_factory=sqlite3.Row
    conn.execute("CREATE TABLE md_records (domain TEXT, record_key TEXT, payload_json TEXT, status TEXT)")
    return conn


def _master(conn,domain,key,payload,status="ACTIVE"):
    conn.execute(
        "INSERT INTO md_records VALUES (?,?,?,?)",
        (domain,key,json.dumps(payload),status),
    )


def _coverage(conn,agent,office,country,port,scope):
    _master(conn,"configuration",f"{agent}-{scope}-{port}",{
        "config_type":"AGENT_NETWORK","network_type":"PORT_COVERAGE",
        "agent_code":agent,"office_code":office,
        "scope":scope,"port_code":port,
    })
    _master(conn,"port",port,{"unlocode":port})


@pytest.mark.parametrize("field,scope", [
    ("POL","POL"),("POD","POD"),("POT (1)","TS"),("POT (2)","TS")
])
def test_cross_agent_booking_port_grant_isolation(monkeypatch,field,scope):
    c=_db()
    # Two independent agent memberships: agent A is Netherlands, agent B Germany.
    memberships={
        (101,"A"): [{"office_code":"RTM","country_code":"NL"}],
        (202,"B"): [{"office_code":"HAM","country_code":"DE"}],
    }
    monkeypatch.setattr(
        "app.admin._assigned_agent_offices",
        lambda conn,user,agent:memberships.get((user,agent),[]),
    )
    _coverage(c,"A","RTM","NL","NLRTM",scope)
    _coverage(c,"B","HAM","DE","DEHAM",scope)
    try:
        a=main.booking_agent_port_grants(c,101,"A")
        b=main.booking_agent_port_grants(c,202,"B")
        assert a[scope]["ports"]=={"NLRTM"}
        assert b[scope]["ports"]=={"DEHAM"}
        assert main.booking_validate_authorized_ports({field:"NLRTM"},a)
        assert main.booking_validate_authorized_ports({field:"DEHAM"},b)
        for grants,unauthorized in ((a,"DEHAM"),(b,"NLRTM")):
            with pytest.raises(HTTPException) as error:
                main.booking_validate_authorized_ports({field:unauthorized},grants)
            assert error.value.status_code==403
            assert error.value.detail["field"]==field
        # An unrelated actor receives no implicit grants.
        unknown=main.booking_agent_port_grants(c,303,"A")
        with pytest.raises(HTTPException) as error:
            main.booking_validate_authorized_ports({field:"NLRTM"},unknown)
        assert error.value.status_code==403
        assert not any(x["all_ports"] for x in (a[scope],b[scope],unknown[scope]))
    finally:
        c.close()


@pytest.mark.parametrize("status",["INACTIVE","DRAFT","PENDING_APPROVAL"])
def test_booking_port_revocation_does_not_leave_cached_access(monkeypatch,status):
    c=_db()
    monkeypatch.setattr(
        "app.admin._assigned_agent_offices",
        lambda conn,user,agent:[{"office_code":"RTM","country_code":"NL"}],
    )
    _coverage(c,"A","RTM","NL","NLRTM","POL")
    try:
        first=main.booking_agent_port_grants(c,101,"A")
        assert main.booking_validate_authorized_ports({"POL":"NLRTM"},first)
        c.execute(
            "UPDATE md_records SET status=? WHERE domain='port' AND record_key='NLRTM'",
            (status,),
        )
        latest=main.booking_agent_port_grants(c,101,"A")
        assert not latest["POL"]["ports"]
        with pytest.raises(HTTPException) as error:
            main.booking_validate_authorized_ports({"POL":"NLRTM"},latest)
        assert error.value.status_code==403
    finally:
        c.close()


def test_booking_port_coverage_does_not_grant_unspecified_scopes(monkeypatch):
    c=_db()
    monkeypatch.setattr(
        "app.admin._assigned_agent_offices",
        lambda conn,user,agent:[{"office_code":"RTM","country_code":"NL"}],
    )
    _coverage(c,"A","RTM","NL","NLRTM","POL")
    try:
        grants=main.booking_agent_port_grants(c,101,"A")
        assert grants["POL"]["ports"]=={"NLRTM"}
        assert grants["POD"]["ports"]==set()
        assert grants["TS"]["ports"]==set()
        for field in ("POD","POT (1)","POT (2)"):
            with pytest.raises(HTTPException) as error:
                main.booking_validate_authorized_ports({field:"NLRTM"},grants)
            assert error.value.status_code==403
    finally:
        c.close()


@pytest.mark.parametrize("revoked_domain", ["configuration", "port"])
@pytest.mark.parametrize("status", ["INACTIVE", "DRAFT", "PENDING_APPROVAL"])
def test_booking_rejects_revoked_network_or_port_master(monkeypatch,revoked_domain,status):
    conn=_db()
    monkeypatch.setattr(
        "app.admin._assigned_agent_offices",
        lambda conn,user,agent:[{"office_code":"RTM","country_code":"NL"}],
    )
    _coverage(conn,"A","RTM","NL","NLRTM","POL")
    try:
        assert main.booking_validate_authorized_ports(
            {"POL":"NLRTM"},main.booking_agent_port_grants(conn,101,"A")
        )
        conn.execute("UPDATE md_records SET status=? WHERE domain=?",(status,revoked_domain))
        grants=main.booking_agent_port_grants(conn,101,"A")
        assert grants["POL"]["ports"]==set()
        with pytest.raises(HTTPException) as e:
            main.booking_validate_authorized_ports({"POL":"NLRTM"},grants)
        assert e.value.status_code==403
    finally:
        conn.close()


def test_multi_office_agent_has_only_explicitly_covered_ports(monkeypatch):
    conn=_db()
    monkeypatch.setattr(
        "app.admin._assigned_agent_offices",
        lambda conn,user,agent:[
            {"office_code":"RTM","country_code":"NL"},
            {"office_code":"HAM","country_code":"DE"},
        ],
    )
    _coverage(conn,"A","RTM","NL","NLRTM","POL")
    _coverage(conn,"A","HAM","DE","DEHAM","POD")
    try:
        grants=main.booking_agent_port_grants(conn,101,"A")
        assert grants["POL"]["ports"]=={"NLRTM"}
        assert grants["POD"]["ports"]=={"DEHAM"}
        assert grants["TS"]["ports"]==set()
        assert main.booking_validate_authorized_ports(
            {"POL":"NLRTM","POD":"DEHAM"},grants
        )
        for fields in (
            {"POL":"DEHAM","POD":"NLRTM"},
            {"POT (1)":"NLRTM"},
            {"POT (2)":"DEHAM"},
        ):
            with pytest.raises(HTTPException) as error:
                main.booking_validate_authorized_ports(fields,grants)
            assert error.value.status_code==403
    finally:
        conn.close()


def test_country_conflict_revokes_same_office_independent_of_order(monkeypatch):
    conn=_db()
    _coverage(conn,"A","RTM","NL","NLRTM","POL")
    try:
        for countries in (("NL","DE"),("DE","NL")):
            monkeypatch.setattr(
                "app.admin._assigned_agent_offices",
                lambda conn,user,agent,countries=countries:[
                    {"office_code":"RTM","country_code":country}
                    for country in countries
                ],
            )
            grants=main.booking_agent_port_grants(conn,101,"A")
            assert grants["POL"]["ports"]==set()
            with pytest.raises(HTTPException) as error:
                main.booking_validate_authorized_ports({"POL":"NLRTM"},grants)
            assert error.value.status_code==403
    finally:
        conn.close()
