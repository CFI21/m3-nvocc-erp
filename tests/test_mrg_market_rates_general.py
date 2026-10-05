from pathlib import Path

def test_mrg_router_and_screen_contract():
    import app.main as main
    paths=set(main.app.openapi()['paths'])
    required={
        '/api/mrg/rules',
        '/api/mrg/rules/{rule_ref}',
        '/api/mrg/rules/{rule_ref}/slabs',
        '/api/mrg/rules/{rule_ref}/approve',
        '/api/mrg/booking/{job_ref}/applicable',
        '/api/mrg/booking/{job_ref}/import',
        '/api/mrg/report',
    }
    assert required <= paths
    from app.screen_catalog import build_catalog
    assert build_catalog()['screen_count']==196

def test_mrg_ui_preserves_existing_rate_management():
    html=Path('web/index.html').read_text()
    for label in [
        "MRG","MRG Export","MRG Import","MRG Transshipment",
        "MRG Detention","MRG Detention Export","Special Detention","MRG MTY Storage"
    ]:
        assert label in html
    assert "Import MRG" in html
    assert "Import Selected" in html
    assert "Import All" in html
    assert "Special Rate Request" in html
    assert "agent-tasks::special-rates-request" in html
    assert "Commercial Charges" in html
    assert "MRG import is optional." in html

def test_mrg_backend_is_shared_and_does_not_post_finance():
    src=Path('app/mrg.py').read_text()
    assert 'mrg_rules' in src
    assert 'mrg_slabs' in src
    assert 'SPECIAL_DETENTION' in src
    assert 'CRT_REQUIRED_AFTER_APPROVAL' in src
    assert 'direct_finance_posting":False' in src
    assert 'special_rate_workflow' in src
    assert 'SUPERSEDED_BY_SPECIAL_RATE' in src
    assert 'Commercial Charges' in src
    assert 'INSERT INTO gl_' not in src
    assert 'INSERT INTO treasury_' not in src
