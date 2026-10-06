from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

def tower_block():
    s=HTML.index("if(view==='tower'){")
    e=HTML.index("}else if(view==='overview'){",s)
    return HTML[s:e]

def test_single_primary_navigation():
    for label in ['Overview','Global Stock','Network','Reposition','Lease','Journey','Depot &amp; M&amp;R','Financials','KPI','Exceptions']:
        assert label in HTML
    assert 'Allocation & Shortage</button>' not in HTML[HTML.index('function equipmentShell(view)'):HTML.index('function equipHeaders()')]

def test_live_status_and_authoritative_model():
    assert 'LIVE M3 EQUIPMENT CONTROL — AUTHORITATIVE CONTAINER MASTER' in HTML
    assert 'External providers OFF · real money OFF' in HTML
    assert 'no duplicate inventory' in HTML

def test_tower_filters_and_grids():
    b=tower_block()
    for x in ['Region','Country','Port','Branch','Agent','Depot','Size-Type','Ownership','Status','Date Horizon']:
        assert x in HTML
    assert 'Global Stock Position' in b
    assert 'Recommended Actions' in b
    assert 'Operational Exceptions' in b
    assert 'Financial & Performance' in b

def test_no_raw_json_in_tower():
    b=tower_block()
    assert 'JSON.stringify' not in b
    assert '<pre class="pre">' not in b

def test_existing_authoritative_apis_only():
    b=tower_block()
    for route in [
      '/api/clx070/container-control/kpis',
      '/api/clx070/container-control/containers?limit=1000',
      '/api/clx071/journey/kpis',
      '/api/clx071/journey/exceptions',
      '/api/clx072/network/position',
      '/api/clx072/network/recommendations',
      '/api/clx073/optimization/kpis',
      '/api/clx073/optimization/exceptions',
      '/api/clx074/mr/kpis',
      '/api/clx074/mr/exceptions',
      '/api/clx075/lease/kpis',
      '/api/clx075/lease/exceptions'
    ]: assert route in b

def test_drill_down_uses_existing_container_filters():
    assert 'state.clx79ContainerFilter' in HTML
    assert "qs.set('status',cf.status)" in HTML
    assert '/api/clx070/container-control/containers?' in HTML

def test_safety_acceptance():
    x=json.loads((ROOT/'CLX079_UI_ACCEPTANCE.json').read_text())
    assert x['REAL_MONEY_READY'] is False
    assert x['REAL_PROVIDER_READY'] is False
    assert x['backend_model_changed'] is False


def test_recommended_action_review_is_wired():
    b=tower_block()
    assert "networkRecommendations=' + " not in b
    assert r'onclick="showEquipmentWorkspace(\'network\')">Review</button>' in b
