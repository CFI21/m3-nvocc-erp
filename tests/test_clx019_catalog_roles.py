import pytest
from fastapi import HTTPException

from app.screen_catalog import build_catalog
from app.screen_integration import quick_actions
from app.main import scope_clause


def test_complete_196_screen_catalog_and_domain_counts():
    catalog=build_catalog()
    screens=catalog['screens']
    assert len(screens)==196
    counts={}
    for s in screens:
        counts[s['domain']]=counts.get(s['domain'],0)+1
    assert counts=={
        'Agent Tasks':19,
        'Treasury / AR-AP':37,
        'Integration & Security':20,
        'General / Administration':75,
        'Master Data':42,
    }


def test_screen_ids_routes_and_menu_are_complete_and_unique():
    catalog=build_catalog()
    screens=catalog['screens']
    ids=[s['screen_id'] for s in screens]
    routes=[s['route'] for s in screens]
    assert len(ids)==len(set(ids))==196
    assert len(routes)==len(set(routes))==196

    menu_ids=[]
    for domain in catalog['menu']:
        for submenu in domain['submenus']:
            menu_ids.extend(submenu['screens'])
    assert len(menu_ids)==196
    assert set(menu_ids)==set(ids)
    assert len(menu_ids)==len(set(menu_ids))


def test_every_screen_has_required_browser_contract():
    for s in build_catalog()['screens']:
        assert s['screen_id']
        assert s['domain']
        assert s['submenu']
        assert s['key']
        assert s['name']
        assert s['route'].startswith('/')
        assert 'quick-view' in s['quick_actions']
        assert 'audit-history' in s['quick_actions']
        assert s['audit']


def test_role_action_visibility_preserves_least_privilege():
    sid='agent-tasks::booking'
    viewer=quick_actions(sid,'VIEWER')['visible_actions']
    agent=quick_actions(sid,'AGENT')['visible_actions']
    finance=quick_actions(sid,'FINANCE')['visible_actions']
    admin=quick_actions(sid,'ADMIN')['visible_actions']

    assert set(viewer) <= {'quick-view','related-records','print','export','audit-history'}
    assert 'create' in agent and 'edit' in agent
    assert 'approve' not in agent and 'release' not in agent
    assert 'approve' in finance
    assert 'release' not in finance
    assert 'approve' in admin and 'release' in admin


def test_agent_and_customer_scope_contract():
    with pytest.raises(HTTPException) as exc:
        scope_clause('AGENT',None,None)
    assert exc.value.status_code==403

    clause,args=scope_clause('AGENT','AGT-001',None)
    assert clause==' AND a.code=?'
    assert args==['AGT-001']

    clause,args=scope_clause('VIEWER',None,'CUS-001')
    assert clause==' AND c.code=?'
    assert args==['CUS-001']

    clause,args=scope_clause('VIEWER',None,None)
    assert clause==''
    assert args==[]
