from app.screen_catalog import build_catalog, HO_UTILITY_ALIASES, DOMAIN_SUBMENU_ORDER

EXPECTED_TOP=[
    'Agent Tasks','HO Tasks','Treasury / AR-AP',
    'Integration & Security','General / Administration','Master Data'
]

def menu_domain(c,name):
    return next(x for x in c['menu'] if x['domain']==name)

def test_top_level_and_agent_order_are_preserved():
    c=build_catalog()
    assert [x['domain'] for x in c['menu']]==EXPECTED_TOP
    agent=menu_domain(c,'Agent Tasks')
    assert [x['name'] for x in agent['submenus']]==['Setup','Transaction']
    assert [x['label'] for x in agent['submenus'][0]['items']]==[
        'Agent','Common Parties','Vessel','Voyage Registration','Commodity','Units','Sales Person'
    ]
    assert len(agent['submenus'][1]['screens'])==19

def test_ho_business_order_and_audit_last():
    c=build_catalog()
    ho=menu_domain(c,'HO Tasks')
    assert [x['name'] for x in ho['submenus']]==['Transaction','Utilities']
    labels=[x['label'] for x in HO_UTILITY_ALIASES]
    assert labels[:2]==['Template / Document Setup','Configuration']
    assert labels[-2:]==['Audit History','Integration Audit']

def test_domain_submenus_follow_business_priority():
    c=build_catalog()
    for domain in ('Treasury / AR-AP','Integration & Security','General / Administration','Master Data'):
        d=menu_domain(c,domain)
        explicit=DOMAIN_SUBMENU_ORDER[domain]
        known=[x['name'] for x in d['submenus'] if x['name'] in explicit]
        priorities=[explicit[x] for x in known]
        assert priorities==sorted(priorities), (domain,known)

def test_audit_screens_are_last_inside_each_submenu():
    c=build_catalog(); by_id={s['screen_id']:s for s in c['screens']}
    for d in c['menu']:
        for sub in d['submenus']:
            ids=sub.get('screens',[])
            flags=[('audit' in by_id[x]['name'].lower() or 'audit' in by_id[x]['key'].lower()) for x in ids]
            if any(flags):
                first=flags.index(True)
                assert all(flags[first:]), (d['domain'],sub['name'])

def test_all_196_real_screens_remain_reachable_once():
    c=build_catalog()
    routed=[]
    for d in c['menu']:
        for sub in d['submenus']:
            routed.extend(sub.get('screens',[]))
    assert c['screen_count']==196
    assert len(routed)==196
    assert len(set(routed))==196
    assert set(routed)=={s['screen_id'] for s in c['screens']}

def test_no_alias_target_is_orphaned():
    c=build_catalog(); ids={s['screen_id'] for s in c['screens']}
    for d in c['menu']:
        for sub in d['submenus']:
            for item in sub.get('items',[]):
                assert item['target'] in ids
