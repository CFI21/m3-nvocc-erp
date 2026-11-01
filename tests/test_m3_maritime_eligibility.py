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
