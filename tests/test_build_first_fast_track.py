import pytest

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.screen_catalog import build_catalog
from app.screen_integration import screen_data,quick_actions,action_route
from app.clx049_booking_bl_workspace import verify as verify_clx049
from app.clx048_admin_masterdata_hardening import verify as verify_clx048
from app.clx047_integration_hardening import verify as verify_clx047
from app.clx046_treasury_hardening import verify as verify_clx046
from app.clx045_gl_reporting import control_summary as gl_control_summary

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'build_first.db')
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH

def test_all_196_existing_screens_load_in_one_completion_sweep(isolated):
    c=build_catalog()
    assert c['screen_count']==196
    failures=[]
    for s in c['screens']:
        try:
            d=screen_data(s['screen_id'],x_role='ADMIN')
            assert isinstance(d.get('rows'),list)
            qa=quick_actions(s['screen_id'],'ADMIN')
            assert 'visible_actions' in qa
            for a in qa['visible_actions']:
                if a in {'quick-view','print','export','related-records','audit-history','email'}: continue
                route=action_route(s['screen_id'],a,1,1)
                assert route.get('mode') in {'EXISTING_API','EXISTING_GOVERNANCE','MASTER_DATA_GOVERNANCE','CLIENT_OR_CLX011','CLX011_SIMULATED'}
        except Exception as e:
            failures.append((s['screen_id'],repr(e)))
    assert not failures, failures[:20]

def test_jobs_50001_50005_one_cross_domain_business_day(isolated):
    op=verify_clx049('AUDITOR')
    assert op['all_jobs_pass'] is True
    md=verify_clx048('AUDITOR')
    assert md['all_jobs_valid'] is True
    tr=verify_clx046('AUDITOR')
    assert tr['all_jobs_present'] and tr['all_have_treasury'] and tr['all_reach_gl_reporting']
    integ=verify_clx047('AUDITOR')
    assert integ['all_jobs_present']
    assert integ['all_have_integration']
    assert integ['all_reach_treasury']
    assert integ['all_reach_gl_reporting']
    gl=gl_control_summary(x_role='AUDITOR',x_m3_session=None)
    assert gl['trial_balance_balanced_vc'] is True
    assert gl['trial_balance_balanced_lc'] is True
    assert gl['live_providers'] is False and gl['real_money'] is False

def test_fast_track_frozen_controls_remain_off(isolated):
    assert verify_clx049('AUDITOR')['live_providers'] is False
    assert verify_clx047('AUDITOR')['live_providers'] is False
    assert verify_clx047('AUDITOR')['real_money'] is False
    assert verify_clx046('AUDITOR')['live_providers'] is False
    assert verify_clx046('AUDITOR')['real_money'] is False
