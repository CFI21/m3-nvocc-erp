"""The one-time Admin setup route can reach its own strong authorization gate."""
from pathlib import Path

def test_setup_route_narrow_exemption():
    source=Path("app/main.py").read_text()
    assert "setup_path=request.url.path=='/api/admin/auth/uat-admin-setup'" in source
    assert "setup_exempt=setup_path and request.method.upper() in {'GET','POST'}" in source
    assert "not setup_exempt and not uat_allowed" in source

def test_setup_endpoint_still_checks_private_token_and_isolated_database():
    source=Path("app/admin.py").read_text()
    assert "secrets.compare_digest(b.setup_token, token)" in source
    assert "len(token) < 32" in source
    assert "not db._uat_target_approved()" in source
    assert "user['status'] != 'INACTIVE'" in source
