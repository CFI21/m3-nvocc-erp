from pathlib import Path

def test_operational_setup_routes_and_sources():
    import app.main as main
    paths=set(main.app.openapi()['paths'])
    for p in [
        '/api/ho-operational-setup/demurrage-applicable-countries',
        '/api/ho-operational-setup/detention-applicable-cities',
        '/api/ho-operational-setup/activities',
        '/api/ho-operational-setup/activity-sequence',
        '/api/ho-operational-setup/tariff-context/{port_code}',
    ]:
        assert p in paths

def test_activity_sequence_reuses_clx071_and_remains_server_enforced():
    from app.clx071_container_journey import STANDARD_FLOW,NEXT,validate_transition
    assert STANDARD_FLOW[0]=='AVAILABLE'
    assert STANDARD_FLOW[-1]=='AVAILABLE'
    assert validate_transition('AVAILABLE','RESERVED') is True
    assert validate_transition('AVAILABLE','LOADED') is False
    src=Path('app/ho_operational_setup.py').read_text()
    assert 'from .clx071_container_journey import STANDARD_FLOW, NEXT' in src
    assert 'CREATE TABLE' not in src
    assert '"server_side_enforcement":True' in src
    assert '"container_journey_changed":False' in src

def test_tariff_applicability_is_configuration_overlay_not_new_engine():
    src=Path('app/ho_operational_setup.py').read_text()
    assert "DEMURRAGE_APPLICABLE_COUNTRY" in src
    assert "DETENTION_APPLICABLE_CITY" in src
    assert "domain='configuration'" in src
    assert "duplicate_demurrage_engine" in src
    assert "duplicate_detention_engine" in src
    assert 'mrg_rules' not in src

def test_ui_has_four_ho_setup_workspaces_without_new_screens():
    html=Path('web/index.html').read_text()
    for x in [
        "label:'Demurrage Applicable Countries'",
        "label:'Detention Applicable Cities'",
        "label:'Activity Sequence'",
        "label:'Activity'",
        "workspace:'demurrage-countries'",
        "workspace:'detention-cities'",
        "workspace:'activity-sequence'",
        "workspace:'activity'",
        "master-data::configurations",
        "agent-tasks::container-activity",
    ]:
        assert x in html,x
    from app.screen_catalog import build_catalog
    assert build_catalog()['screen_count']==196
