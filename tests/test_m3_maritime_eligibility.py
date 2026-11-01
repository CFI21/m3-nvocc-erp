"""Offline M3 maritime eligibility regression. No network or database writes."""
import json
from unittest.mock import MagicMock

import pytest

from app.maritime_eligibility import maritime_eligibility, maritime_port_error
from app.masterdata import reference_errors


@pytest.mark.parametrize("function, expected", [
    ("1-------", "ELIGIBLE"),
    ("12------", "ELIGIBLE"),
    ("--3-----", "NOT_ELIGIBLE"),
    ("---4----", "NOT_ELIGIBLE"),
    ("-------8", "NOT_ELIGIBLE"),
    ("--------", "UNKNOWN"),
    ("", "UNKNOWN"),
    (None, "UNKNOWN"),
    ("1------", "UNKNOWN"),
    ("garbage", "UNKNOWN"),
])
def test_unlocode_maritime_function(function, expected):
    assert maritime_eligibility(function) == expected


def test_unknown_and_nonmaritime_fail_closed():
    assert maritime_port_error({"function": ""}) == "MARITIME_PORT_FUNCTION_UNKNOWN"
    assert maritime_port_error({"function": "---4----"}) == "MARITIME_PORT_NOT_ELIGIBLE"
    assert maritime_port_error({"function": "1-------"}) is None


@pytest.mark.parametrize("function, expected", [
    ("1-------", []),
    ("---4----", ["POL_MARITIME_PORT_NOT_ELIGIBLE", "POD_MARITIME_PORT_NOT_ELIGIBLE"]),
    ("", ["POL_MARITIME_PORT_FUNCTION_UNKNOWN", "POD_MARITIME_PORT_FUNCTION_UNKNOWN"]),
])
def test_new_route_requires_maritime_eligible_ports(function, expected):
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [
        {"payload_json": json.dumps({"function": function})},
        {"payload_json": json.dumps({"function": function})},
    ]
    assert reference_errors(conn, "route", {"pol":"NLRTM","pod":"DEHAM"}) == expected


def test_nonmaritime_is_never_interpreted_as_seaport():
    assert maritime_eligibility("-------8") == "NOT_ELIGIBLE"


@pytest.mark.parametrize("changed_function, expected_code", [
    ("---4----", "POL_MARITIME_PORT_NOT_ELIGIBLE"),
    ("", "POL_MARITIME_PORT_FUNCTION_UNKNOWN"),
])
def test_checker_rechecks_port_function_after_maker_submission(tmp_path, monkeypatch, changed_function, expected_code):
    """Actual offline SQLite lifecycle: a late port change must prevent route approval."""
    import sqlite3
    from fastapi import HTTPException
    from app import masterdata as md

    db_path = tmp_path / "maritime-checker.sqlite3"

    def connect():
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    conn = connect()
    md.init_schema(conn)
    for key in ("NLRTM", "DEHAM"):
        payload = json.dumps({"name": key, "unlocode": key, "function": "1-------"})
        conn.execute(
            "INSERT INTO md_records(domain,record_key,display_name,payload_json,status,created_at,updated_at) "
            "VALUES ('port',?,?,?,'ACTIVE','now','now')",
            (key, key, payload),
        )
    conn.close()

    identities = {
        "maker": ({"user_ref": "TEST-MAKER", "username": "md.maker"}, {"MASTER_DATA"}),
        "checker": ({"user_ref": "TEST-CHECKER", "username": "md.checker"}, {"MASTER_DATA_MANAGER"}),
    }
    monkeypatch.setattr(md, "connect", connect)
    monkeypatch.setattr(md, "using_postgres", lambda: False)
    monkeypatch.setattr(md, "authenticated_actor", lambda _c, token: identities[token])

    change = md.create_change(
        md.Change(
            domain="route", record_key="TEST-ROUTE-001", operation="CREATE",
            payload={"name": "Offline maritime route", "pol": "NLRTM", "pod": "DEHAM"},
            reason="Offline metadata drift regression",
        ),
        x_m3_session="maker",
    )
    ref = change["change_ref"]
    assert change["status"] == "PENDING"

    conn = connect()
    conn.execute(
        "UPDATE md_records SET payload_json=? WHERE domain='port' AND record_key='NLRTM'",
        (json.dumps({"name": "NLRTM", "unlocode": "NLRTM", "function": changed_function}),),
    )
    conn.close()

    with pytest.raises(HTTPException) as raised:
        md.decide(ref, md.Decision(decision="APPROVE"), x_m3_session="checker")
    assert raised.value.status_code == 422
    assert expected_code in raised.value.detail["codes"]

    conn = connect()
    assert conn.execute(
        "SELECT status FROM md_change_requests WHERE change_ref=?", (ref,)
    ).fetchone()["status"] == "PENDING"
    assert conn.execute(
        "SELECT COUNT(*) FROM md_records WHERE domain='route' AND record_key='TEST-ROUTE-001'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM md_versions WHERE domain='route' AND record_key='TEST-ROUTE-001'"
    ).fetchone()[0] == 0
    events = [x["action"] for x in conn.execute(
        "SELECT action FROM md_audit WHERE change_ref=?", (ref,)
    )]
    assert events.count("CHANGE_REQUEST_CREATE") == 1
    assert "CHANGE_APPROVE_APPLY" not in events
    conn.close()
