from pathlib import Path

def test_agent_user_alignment_source_contract():
    admin=Path('app/admin.py').read_text()
    web=Path('web/index.html').read_text()
    assert "/agents/{agent_code}/users" in admin
    assert "/agents/{agent_code}/users/requests" in admin
    assert "/agent-user-requests/{review_ref}/decision" in admin
    assert "FOUR_EYES_VIOLATION" in admin
    assert "party_type,'AGENT'" in admin or "party_type='AGENT'" in admin
    assert "status='INACTIVE'" in admin
    assert "mfa_required" in admin
    assert "renderAgentUsersChild" in web
    assert "Shared generic logins are not created" in web

def test_no_duplicate_agent_user_model():
    schema=Path('app/schema.sql').read_text()
    assert 'CREATE TABLE IF NOT EXISTS iam_users' in schema
    assert 'CREATE TABLE IF NOT EXISTS iam_party_access' in schema
    assert 'CREATE TABLE IF NOT EXISTS iam_user_roles' in schema
    assert 'agent_user' not in schema.lower()
