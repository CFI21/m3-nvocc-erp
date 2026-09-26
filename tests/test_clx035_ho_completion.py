import pytest
from fastapi import HTTPException

from app.clx035_ho_completion import aliases, resolve, verification
from app.screen_catalog import build_catalog, HO_TRANSACTION_ALIASES, HO_UTILITY_ALIASES
from app.screen_integration import ADMIN_TABLE, MD_DOMAIN_MAP


def test_clx035_preserves_193_and_all_33_ho_aliases():
    v=verification()
    assert v['status']=='PASS'
    assert v['screen_count']==193
    assert v['transaction_aliases']==18
    assert v['utility_aliases']==15
    assert v['total_aliases']==33
    assert v['missing_targets']==[]
    assert v['duplicate_screens_added']==0


def test_every_alias_reuses_existing_route_actions_and_quick_view():
    c=build_catalog()
    by_id={s['screen_id']:s for s in c['screens']}
    for item in HO_TRANSACTION_ALIASES+HO_UTILITY_ALIASES:
        target=by_id[item['target']]
        assert target['route'].startswith('/')
        assert target['actions']
        assert 'quick-view' in target['quick_actions']


def test_verified_utility_runtime_mapping_gaps_are_fixed():
    assert MD_DOMAIN_MAP['configurations']=='configuration'
    assert ADMIN_TABLE['audit']=='iam_audit_events'


def test_sensitive_ho_utilities_have_explicit_alias_roles():
    by_label={x['label']:x for x in HO_UTILITY_ALIASES}
    for label in ('User Management','Security Policy','Password Policy','MFA Policy','Data Scope / Policy','Audit History','Integration Audit','Approval Limits','Approval Queue'):
        assert by_label[label].get('roles'), label


def test_viewer_cannot_resolve_user_management_alias():
    with pytest.raises(HTTPException) as exc:
        resolve('User Management','VIEWER')
    assert exc.value.status_code==403
    assert exc.value.detail['code']=='HO_TARGET_ROLE_DENIED'


def test_admin_can_resolve_user_management_with_existing_target():
    r=resolve('User Management','ADMIN')
    assert r['target']=='administration::users'
    assert r['target_route']
    assert r['screen_count']==193


def test_alias_listing_applies_role_filter_without_changing_catalog():
    viewer=aliases(role='VIEWER')
    admin=aliases(role='ADMIN')
    assert viewer['screen_count']==193
    assert admin['screen_count']==193
    assert admin['navigation_alias_count']>=viewer['navigation_alias_count']
    assert not any(x['label']=='User Management' for x in viewer['items'])
    assert any(x['label']=='User Management' for x in admin['items'])
