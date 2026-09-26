from app.screen_catalog import build_catalog, AGENT_SETUP_ALIASES
from app.screen_integration import MD_DOMAIN_MAP

EXPECTED_SETUP=['Agent','Common Parties','Vessel','Voyage Registration','Commodity','Units','Sales Person']

def test_agent_tasks_starts_with_setup_then_transaction():
    c=build_catalog()
    agent=next(x for x in c['menu'] if x['domain']=='Agent Tasks')
    assert [x['name'] for x in agent['submenus']]==['Setup','Transaction']
    assert [x['label'] for x in agent['submenus'][0]['items']]==EXPECTED_SETUP
    assert len(agent['submenus'][1]['screens'])==19

def test_setup_aliases_all_resolve_to_existing_screens():
    c=build_catalog(); ids={s['screen_id'] for s in c['screens']}
    assert all(x['target'] in ids for x in AGENT_SETUP_ALIASES)

def test_new_setup_masters_are_authoritative_generic_master_domains():
    assert MD_DOMAIN_MAP['common-partys']=='common-party'
    assert MD_DOMAIN_MAP['units']=='unit'
    assert MD_DOMAIN_MAP['sales-persons']=='sales-person'
    assert MD_DOMAIN_MAP['commoditys']=='commodity'

def test_only_three_additive_master_screens_are_added():
    c=build_catalog()
    assert c['screen_count']==196
