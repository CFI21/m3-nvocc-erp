import pytest

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.clx036_ho_business_day import verify, trace, HO_REQUIRED_LABELS
from app.screen_catalog import build_catalog
from app.clx034_smart_approval_fast_track import policy as smart_policy


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'clx036.db')
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH


def test_complete_ho_business_day_trace_passes_for_50001_50005(isolated_db):
    out=verify()
    assert out['status']=='PASS'
    assert out['canonical_jobs']==['50001','50002','50003','50004','50005']
    assert out['screen_count']==196
    assert out['ho_transaction_aliases']==18
    assert [x['label'] for x in out['ho_routes']]==HO_REQUIRED_LABELS
    assert all(x['resolved'] for x in out['ho_routes'])
    assert all(x['pass'] for x in out['jobs'])


def test_each_job_has_no_orphans_duplicates_and_balanced_gl(isolated_db):
    for jr in ['50001','50002','50003','50004','50005']:
        x=trace(jr)
        assert x['pass'] is True
        assert x['links']['booking']==1
        assert x['links']['containers']>=1
        assert x['links']['bills']>=2
        assert x['links']['agent_tasks']==19
        assert x['links']['gl']>=30
        assert x['links']['treasury']==37
        assert x['links']['integration']==20
        assert x['required_modules']['agent_missing']==[]
        assert x['required_modules']['gl_missing']==[]
        assert x['required_modules']['integration_missing']==[]
        assert x['gl_balance']['balanced'] is True
        assert x['duplicates']=={'agent':0,'gl':0,'treasury':0}
        assert x['orphan_source_refs']==0
        assert x['master_refs']['customer'] is True
        assert x['master_refs']['agent'] is True
        assert x['master_refs']['carrier_count']>0
        assert x['master_refs']['document_types']>0


def test_ho_trace_preserves_office_country_scope(isolated_db):
    expected={
      '50001':('RTM','NL'),'50002':('DXB','AE'),'50003':('SHA','CN'),
      '50004':('KHI','PK'),'50005':('RTM','NL')
    }
    for jr,pair in expected.items():
        x=trace(jr)
        assert (x['office'],x['country'])==pair


def test_clx034_exception_based_governance_remains_active(isolated_db):
    p=smart_policy()
    assert p['mode']=='EXCEPTION_BASED_GOVERNANCE'
    assert p['live_providers'] is False
    assert p['real_money'] is False


def test_no_screen_growth_or_business_model_change(isolated_db):
    c=build_catalog()
    assert c['screen_count']==196
    out=verify()
    assert out['data_model_changed'] is False
    assert out['api_contracts']=='PRESERVED_WITH_ADDITIVE_CLX036_ENDPOINTS'
    assert out['live_providers'] is False
    assert out['real_money'] is False
