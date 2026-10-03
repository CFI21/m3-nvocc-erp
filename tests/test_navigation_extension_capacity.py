from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

from app.screen_catalog import build_catalog, BUSINESS_NAV_ALIASES
from app.screen_integration import menu, screen_role_allowed

def test_current_baseline_is_196_but_extensible():
    c=build_catalog()
    assert c['screen_count']==196
    assert c['baseline_screen_count']==196
    assert c['extension_count']==0
    assert 'not a permanent hard limit' in c['screen_count_policy']
    assert c['extension_governance']['placeholder_screens_forbidden'] is True
    assert c['extension_governance']['duplicate_models_forbidden'] is True

def test_business_navigation_uses_existing_screens_only():
    c=build_catalog()
    ids={s['screen_id'] for s in c['screens']}
    assert [x['domain'] for x in c['menu']]==[
        'HO Tasks','Agent Tasks','Treasury / AR-AP',
        'Integration & Security','General / Administration','Master Data'
    ]
    business=c['business_navigation']
    assert business['navigation_only'] is True
    assert business['extensible'] is True
    assert [g['name'] for g in business['groups']]==[
        'Setup','Transaction / Operations','Document','Equipment','Finance','Control / Reporting'
    ]
    for group in business['groups']:
        for item in group['items']:
            assert item['target'] in ids
    assert len(ids)==196

def test_required_findability_synonyms():
    expected={
      'do':'agent-tasks::delivery-order',
      'delivery order':'agent-tasks::delivery-order',
      'b/l':'agent-tasks::bl',
      'bl':'agent-tasks::bl',
      'hbl':'agent-tasks::bl',
      'mbl':'agent-tasks::bl',
      'cro':'agent-tasks::cro',
      'crt':'agent-tasks::crt',
      'soa':'agent-tasks::soa',
      'statement of account':'agent-tasks::soa',
      'job':'agent-tasks::planning',
    }
    for q,target in expected.items():
        m=menu(q)
        found=set()
        for d in m['menu']:
            for sub in d['submenus']:
                found.update(x['screen_id'] for x in sub.get('links',[]))
                found.update(x['target'] for x in sub.get('items',[]))
        assert target in found,(q,target,found)

def test_screen_contract_exposes_extension_policy_and_search_terms():
    c=build_catalog()
    booking=next(s for s in c['screens'] if s['screen_id']=='agent-tasks::booking')
    assert 'bkg' in booking['search_terms']
    assert booking['extension_policy']['field_extension']=='ADD_TO_EXISTING_SCREEN_FIRST'
    assert booking['extension_policy']['section_extension']=='ADD_SECTION_OR_TAB_WHEN_SAME_BUSINESS_FUNCTION'
    assert booking['extension_policy']['new_screen_extension']=='ONLY_FOR_VERIFIED_DISTINCT_BUSINESS_WORKSPACE'

def test_role_visibility_is_not_bypassed_by_aliases():
    c=build_catalog()
    gl=next(s for s in c['screens'] if s['screen_id']=='gl-accounts::invoice')
    assert screen_role_allowed(gl,'GL_ACCOUNTANT')
    assert not screen_role_allowed(gl,'AGENT')
    # Alias navigation never creates broader target roles.
    finance=BUSINESS_NAV_ALIASES['Finance']
    assert next(x for x in finance if x['target']=='gl-accounts::invoice')['target']==gl['screen_id']

def test_previous_current_next_flow_present_for_core_operations():
    for x in [
      'const CORE_BUSINESS_FLOW=',
      "'agent-tasks::booking':{previous:",
      "'agent-tasks::planning':{previous:",
      "'agent-tasks::bl':{previous:",
      "'agent-tasks::cro':{previous:",
      "'agent-tasks::crt':{previous:",
      "'agent-tasks::container-activity':{previous:",
      "'agent-tasks::delivery-order':{previous:",
      '<b>Previous</b>','<b>Current</b>','<b>Next</b>',
      'Condition-driven / none'
    ]:
        assert x in HTML,x

def test_frontend_search_uses_catalog_synonyms():
    assert "...(s.search_terms||[])" in HTML
    assert "...(it.search_terms||[])" in HTML
    assert "state.businessNavigation=m.business_navigation" in HTML
    assert "Business Navigation" in HTML
    assert "screenAllowed(s)" in HTML

def test_forms_remain_extension_safe_dense_layout():
    assert '.clx78Fields,.clx78Commercial .clx78Fields{grid-template-columns:repeat(2' in HTML
    assert '.clx82Grid{grid-template-columns:repeat(2' in HTML
    assert '.formField.full{grid-column:1/-1}' in HTML
    assert 'No empty future sections are rendered' in HTML

def test_acceptance_safety():
    a=json.loads((ROOT/'M3_NAVIGATION_EXTENSION_ACCEPTANCE.json').read_text())
    assert a['screen_count']==196
    assert a['new_screens']==[]
    assert a['new_fields']==[]
    assert a['database_changed'] is False
    assert a['api_contract_breaking_change'] is False
    assert a['real_money'] is False
    assert a['providers']=={'TRUE_LAYER':False,'OXR':False,'AVALARA':False}
    assert a['ancline_touched'] is False
