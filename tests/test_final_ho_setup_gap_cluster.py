from pathlib import Path

def test_final_ho_setup_routes_and_no_duplicate_engines():
    import app.main as main
    paths=set(main.app.openapi()['paths'])
    for p in [
      '/api/final-ho-setup/damage-setup',
      '/api/final-ho-setup/container-status',
      '/api/final-ho-setup/local-recovery-agreements',
      '/api/final-ho-setup/local-recovery/booking/{job_ref}/applicable',
      '/api/final-ho-setup/local-recovery/booking/{job_ref}/apply',
      '/api/final-ho-setup/long-ageing',
    ]:
        assert p in paths
    src=Path('app/final_ho_setup.py').read_text()
    assert 'CREATE TABLE' not in src
    assert '"duplicate_mr_engine":False' in src
    assert '"duplicate_status_engine":False' in src
    assert '"direct_finance_posting":False' in src
    assert '"equipment_network_changed":False' in src
    assert '"server_side_evaluation":True' in src

def test_container_status_reuses_clx071_transition_model():
    from app.final_ho_setup import container_status
    d=container_status()
    assert d['source']=='CLX071 / Container Master'
    assert d['duplicate_status_engine'] is False
    by={x['status_code']:x for x in d['records']}
    assert 'RESERVED' in by['AVAILABLE']['allowed_next_status']
    assert 'LOADED' not in by['AVAILABLE']['allowed_next_status']
    assert by['AVAILABLE']['available_for_booking'] is True
    assert by['HOLD']['hold_state'] is True

def test_customer_tariff_is_filtered_existing_mrg_and_preserves_booking_import():
    html=Path('web/index.html').read_text()
    for x in [
      "label:'Customer Tariff'",
      "openCustomerTariffWorkspace",
      "RATE_SIDE=REVENUE",
      "PARTY_TYPE=CUSTOMER",
      "rate_side:customerTariffMode?'REVENUE'",
      "party_type:customerTariffMode?'CUSTOMER'",
      "Import MRG",
      "agent-tasks::special-rates-request",
    ]:
        assert x in html,x
    mrg=Path('app/mrg.py').read_text()
    assert 'CRT_REQUIRED_AFTER_APPROVAL' in mrg
    assert 'SUPERSEDED_BY_SPECIAL_RATE' in mrg
    assert 'Commercial Charges' in mrg

def test_final_setup_ui_and_screen_count():
    html=Path('web/index.html').read_text()
    for x in [
      "label:'Damage Setup'",
      "label:'Container Status'",
      "label:'Customer Tariff'",
      "label:'Local Recovery Agreement'",
      "label:'Long Ageing Configuration'",
      "workspace:'damage-setup'",
      "workspace:'container-status'",
      "workspace:'local-recovery'",
      "workspace:'long-ageing'",
      "showEquipmentWorkspace('mr')",
      "showEquipmentWorkspace('network')",
    ]:
        assert x in html,x
    from app.screen_catalog import build_catalog
    assert build_catalog()['screen_count']==196

def test_existing_authoritative_models_not_replaced():
    src=Path('app/final_ho_setup.py').read_text()
    assert 'container_damage_items' in src
    assert 'STANDARD_FLOW' in src and 'NEXT' in src
    assert 'Commercial Charges' in src
    assert 'LOCAL_RECOVERY' in src
    assert 'CRT_REQUIRED_AFTER_APPROVAL' in src
    assert 'direct_finance_posting":False' in src
    assert 'scoped_containers' in src
    assert 'owner_segment' in src
