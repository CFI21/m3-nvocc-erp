from pathlib import Path
import pytest
from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run
from app.screen_catalog import build_catalog
from app.screen_integration import screen_data, quick_actions, action_route

HTML=Path("web/index.html").read_text()

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'clx043.db')
    seed_run(True)
    masterdata_seed_run()
    admin_seed_run()
    return db.DB_PATH

def test_frozen_baseline_and_menu_order():
    c=build_catalog()
    assert c['screen_count']==196
    assert [x['domain'] for x in c['menu']]==[
      'Agent Tasks','HO Tasks','Treasury / AR-AP',
      'Integration & Security','General / Administration','Master Data'
    ]

def test_every_catalog_screen_loads_without_server_error(isolated):
    for s in build_catalog()['screens']:
        out=screen_data(s['screen_id'],x_role='ADMIN')
        assert out['screen']['screen_id']==s['screen_id']
        assert isinstance(out['rows'],list)

def test_verified_dashboards_and_master_audit_are_bound(isolated):
    assert screen_data('administration::dashboard',x_role='ADMIN')['count']>0
    assert screen_data('master-data::dashboard',x_role='ADMIN')['count']>0
    assert screen_data('master-data::audit',x_role='ADMIN')['count']>0

def test_gl_treasury_mutations_route_to_real_v1_apis():
    assert action_route('gl-accounts::voucher','approve',1,1)['path'].startswith('/api/v1/gl/')
    assert action_route('gl-accounts::voucher','edit',1,1)['path'].startswith('/api/v1/gl/')
    assert action_route('treasury::cashbook','approve',1,1)['path'].startswith('/api/v1/treasury/')
    assert action_route('treasury::payment-batch','edit',1,1)['path'].startswith('/api/v1/treasury/')

def test_no_visible_action_falls_back_to_dead_placeholder():
    mutation={'create','edit','approve','release','hold','cancel','amend','reissue','reverse','retry','activate','deactivate','change-request','reject','advance'}
    for s in build_catalog()['screens']:
        for a in quick_actions(s['screen_id'],'ADMIN')['visible_actions']:
            if a not in mutation: continue
            route=action_route(s['screen_id'],a,1,1)
            if s['domain']=='Master Data':
                assert route['mode']=='EXISTING_GOVERNANCE'
            else:
                assert route['mode']=='EXISTING_API', (s['screen_id'],a,route)

def test_html_has_domain_specific_functional_handlers():
    assert "openAgentForm('create')" in HTML
    assert "openFinanceForm('create')" in HTML
    assert "saveFinanceForm(mode)" in HTML
    assert "executeFinanceAction(a)" in HTML
    assert "openMasterChange()" in HTML
    assert "saveMasterChange(isUpdate)" in HTML
    assert "decideMasterChange(decision)" in HTML
    assert "--nav:#4b5563" in HTML
