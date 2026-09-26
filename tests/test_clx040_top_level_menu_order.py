from app.screen_catalog import build_catalog

EXPECTED_TOP=[
    'Agent Tasks',
    'HO Tasks',
    'Treasury / AR-AP',
    'Integration & Security',
    'General / Administration',
    'Master Data',
]

def test_top_level_menu_business_order():
    c=build_catalog()
    assert [x['domain'] for x in c['menu']]==EXPECTED_TOP

def test_agent_setup_transaction_order_preserved():
    c=build_catalog()
    agent=c['menu'][0]
    assert [x['name'] for x in agent['submenus']]==['Setup','Transaction']
    assert [x['label'] for x in agent['submenus'][0]['items']]==[
        'Agent','Common Parties','Vessel','Voyage Registration','Commodity','Units','Sales Person'
    ]
    assert len(agent['submenus'][1]['screens'])==19

def test_ho_transaction_utilities_order_preserved():
    c=build_catalog()
    ho=c['menu'][1]
    assert [x['name'] for x in ho['submenus']]==['Transaction','Utilities']

def test_screen_baseline_unchanged():
    assert build_catalog()['screen_count']==196
