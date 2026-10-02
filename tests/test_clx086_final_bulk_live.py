from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')
MAIN=(ROOT/'app/main.py').read_text(encoding='utf-8')

def j(name): return json.loads((ROOT/name).read_text())

def test_exact_parent_and_noop_bulk():
    b=j('CLX086_BULK_IMPORT_REPORT.json')
    assert b['parent_baseline']=='cc4963e73dcad8d584218b0a5aab445318a61885'
    assert b['status']=='PASS_NO_INPUT'
    assert b['source_files']==b['create_count']==b['update_count']==b['reject_count']==0
    assert b['no_reseed'] is True
    assert '/api/masterdata/changes' in b['authoritative_route_policy']

def test_production_reconciliation_evidence():
    r=j('CLX086_DATA_RECONCILIATION.json')
    assert r['status']=='PASS'
    assert all(v==0 for v in r['duplicates'].values())
    assert all(v==0 for v in r['orphans'].values())
    assert r['finance']['gl_variance']=='0.00'
    assert r['provider_safety']['real_provider_rows_enabled']==0
    assert r['provider_safety']['real_money_provider_rows_enabled']==0

def test_security_and_provider_lock():
    s=j('CLX086_SECURITY_UAT.json')
    assert s['all_listed_public_tables_rls_enabled'] is True
    assert s['provider_state']['live_real_providers']=='OFF'
    assert s['provider_state']['real_money']=='OFF'
    assert s['maker_checker']=='PRESERVED' and s['four_eyes']=='PRESERVED'

def test_final_build_markers_and_targets():
    assert "CLX086_FINAL_BUILD_BASELINE='cc4963e73dcad8d584218b0a5aab445318a61885'" in MAIN
    assert "version='0.86.0'" in MAIN
    assert 'CLX-086 Final Bulk-to-Live Candidate' in HTML
    assert "CLX-080" in HTML
    s=j('CLX086_PRODUCTION_SMOKE.json')
    assert s['api_service_id']=='srv-dar9fh17lnhs73ahlh60'
    assert s['web_service_id']=='srv-darbgkvlk1mc738rohng'
    assert 'ancline' not in json.dumps(s).lower()

def test_canonical_196_screen_and_prior_acceptance():
    from app.screen_catalog import build_catalog
    c=build_catalog()
    assert c['screen_count']==196
    for name,key in [
      ('CLX081_UI_ACCEPTANCE.json','BOOKING_MASTER_READY'),
      ('CLX082_UI_ACCEPTANCE.json','BL_MASTER_READY'),
      ('CLX083_UI_ACCEPTANCE.json','OPERATIONAL_DOCUMENT_FLOW_READY'),
      ('CLX084_SOA_UI_ACCEPTANCE.json','FINANCE_MASTER_FLOW_READY'),
      ('CLX085_UI_ACCEPTANCE.json','MANAGEMENT_CONTROL_READY')]:
        assert j(name)[key] is True

def test_safety_no_real_money_or_provider_activation():
    a=j('CLX086_FINAL_LIVE_ACCEPTANCE.json')
    assert a['live_providers']=='OFF' and a['real_money']=='OFF'
    render=(ROOT/'render.yaml').read_text()
    assert 'M3_LIVE_PROVIDERS' in render and 'REAL_MONEY' in render
    assert 'value: "OFF"' in render
