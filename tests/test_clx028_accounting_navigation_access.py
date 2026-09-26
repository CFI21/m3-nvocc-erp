from app.screen_catalog import build_catalog
from app.gl import actor

def test_gl_is_under_general_administration_not_agent_tasks():
    catalog=build_catalog()
    gl=[s for s in catalog['screens'] if s['screen_id'].startswith('gl-accounts::')]
    assert gl
    assert all(s['domain']=='General / Administration' for s in gl)
    assert all(s['submenu'].startswith('Finance & Accounting Setup · ') for s in gl)
    assert not any(s['domain']=='Agent Tasks' and s['screen_id'].startswith('gl-accounts::') for s in catalog['screens'])

def test_gl_roles_are_explicit_and_agent_ops_finance_are_not_implicitly_authorized():
    assert actor('GL_ACCOUNTANT','view')=='GL_ACCOUNTANT'
    assert actor('GL_ACCOUNTANT','create')=='GL_ACCOUNTANT'
    assert actor('GL_MANAGER','approve')=='GL_MANAGER'
    for role in ('AGENT','OPS','FINANCE','VIEWER'):
        try:
            actor(role,'view')
            assert False, f'{role} unexpectedly received GL access'
        except Exception as exc:
            assert getattr(exc,'status_code',None)==403

def test_gl_accountant_cannot_approve():
    try:
        actor('GL_ACCOUNTANT','approve')
        assert False, 'GL_ACCOUNTANT unexpectedly approved'
    except Exception as exc:
        assert getattr(exc,'status_code',None)==403
