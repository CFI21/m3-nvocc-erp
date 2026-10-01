from pathlib import Path

def test_final_container_control_router_is_mounted():
    import app.main as main
    paths=set(main.app.openapi()['paths'])
    required={
      '/api/clx070/container-control/policies',
      '/api/clx070/container-control/sops',
      '/api/clx070/container-control/containers',
      '/api/clx070/container-control/containers/{container_no}',
      '/api/clx070/container-control/booking-context/{job_ref}',
      '/api/clx070/container-control/kpis',
      '/api/clx070/container-control/bulk-register',
    }
    assert required <= paths

def test_booking_equipment_is_native_m3_tab():
    html=Path('web/index.html').read_text()
    assert "CLX49_BOOKING_TABS=['Booking Info','Equipment','Other Info']" in html
    assert "booking-context/" in html
    assert "One M3 Equipment Control Flow" in html
    assert "Container P&amp;L" in html
    assert "Policy &amp; SOP" in html

def test_container_master_scope_covers_legacy_and_new_requirements():
    sql=Path('migrations/CLX070_002_container_master_global_governance.sql').read_text()
    required=[
      'owner_party_type','owner_party_code','csc_validity','csc_plate_no','csc_safety_approval_no',
      'manufacturing_no','manufacturer','max_gross_weight','tare_weight','classification','grade_payload',
      'stacking_weight','iso_code','test_load','machinery','periodic_inspection_due_date','tank_kind',
      'allocate_for_sale','afghan_transit','purchase_invoice_no','sale_invoice_no',
      'container_party_links','container_documents','container_financial_ledger','container_share_rules',
      'equipment_system_policies'
    ]
    for marker in required:
        assert marker in sql

def test_one_container_master_and_safety_guards_are_documented():
    doc=Path('CLX070_FINAL_CONTAINER_MASTER_SCOPE.md').read_text()
    assert 'one M3 NVOCC ERP' in doc
    assert 'one containers table' in doc
    assert 'Production traffic, live providers and real-money execution remain OFF' in doc
