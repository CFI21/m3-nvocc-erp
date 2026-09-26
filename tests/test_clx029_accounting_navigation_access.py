import pytest
from fastapi import HTTPException

from app.screen_catalog import build_catalog
from app.gl import actor
from app.screen_integration import quick_actions, screen_role_allowed


def test_gl_is_general_administration_finance_setup_not_agent_tasks():
    catalog=build_catalog()
    gl=[s for s in catalog['screens'] if s['screen_id'].startswith('gl-accounts::')]
    assert len(gl)==47
    assert all(s['domain']=='General / Administration' for s in gl)
    assert all(s['submenu'].startswith('Finance & Accounting Setup · ') for s in gl)
    assert not any(s['domain']=='Agent Tasks' and s['screen_id'].startswith('gl-accounts::') for s in catalog['screens'])
    assert len(catalog['screens'])==196


def test_gl_runtime_requires_explicit_accounting_role():
    assert actor('GL_ACCOUNTANT','view')=='GL_ACCOUNTANT'
    assert actor('GL_ACCOUNTANT','create')=='GL_ACCOUNTANT'
    assert actor('GL_MANAGER','approve')=='GL_MANAGER'
    for role in ('AGENT','OPS','FINANCE','VIEWER'):
        with pytest.raises(HTTPException) as exc:
            actor(role,'view')
        assert exc.value.status_code==403


def test_gl_accountant_is_maker_not_checker():
    with pytest.raises(HTTPException) as exc:
        actor('GL_ACCOUNTANT','approve')
    assert exc.value.status_code==403


def test_screen_navigation_enforces_gl_role_visibility():
    gl=next(s for s in build_catalog()['screens'] if s['screen_id'].startswith('gl-accounts::'))
    assert screen_role_allowed(gl,'ADMIN')
    assert screen_role_allowed(gl,'GL_MANAGER')
    assert screen_role_allowed(gl,'GL_ACCOUNTANT')
    assert screen_role_allowed(gl,'AUDITOR')
    assert not screen_role_allowed(gl,'FINANCE')
    assert not screen_role_allowed(gl,'OPS')
    assert not screen_role_allowed(gl,'AGENT')


def test_quick_actions_do_not_give_gl_accountant_approval():
    sid=next(s['screen_id'] for s in build_catalog()['screens'] if s['screen_id'].startswith('gl-accounts::'))
    actions=quick_actions(sid,'GL_ACCOUNTANT')['visible_actions']
    assert 'approve' not in actions
