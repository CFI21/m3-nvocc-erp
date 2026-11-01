"""Fail-closed maritime location classification for new M3 routing assignments.

UNECE function codes use a nine-character positional flag string. Position 1
is the seaport function ("1"); position 8 ("8") means inland waterway, not
a maritime seaport. Never infer maritime eligibility from a valid UN/LOCODE.
"""
def maritime_eligibility(function):
    value = str(function or "").strip()
    if not value:
        return "UNKNOWN"
    if len(value) != 9 or any(c not in "-0123456789" for c in value):
        return "UNKNOWN"
    if value[0] == "1":
        return "ELIGIBLE"
    if value[0] != "-":
        return "UNKNOWN"
    if any(c != "-" for c in value[1:]):
        return "NOT_ELIGIBLE"
    return "UNKNOWN"


def maritime_port_error(port_payload):
    status = maritime_eligibility(port_payload.get("function"))
    if status == "ELIGIBLE":
        return None
    return "MARITIME_PORT_FUNCTION_UNKNOWN" if status == "UNKNOWN" else "MARITIME_PORT_NOT_ELIGIBLE"
