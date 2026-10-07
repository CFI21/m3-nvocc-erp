from pathlib import Path

def test_clx071_router_is_mounted():
    import app.main as main
    paths=set(main.app.openapi()["paths"])
    required={
      "/api/clx071/journey/policies",
      "/api/clx071/journey/containers/{container_no}/plan",
      "/api/clx071/journey/containers/{container_no}/events",
      "/api/clx071/journey/containers/{container_no}",
      "/api/clx071/journey/scan-exceptions",
      "/api/clx071/journey/exceptions",
      "/api/clx071/journey/exceptions/{exception_ref}/decision",
      "/api/clx071/journey/kpis",
    }
    assert required <= paths

def test_standard_lifecycle_and_variants():
    from app.clx071_container_journey import validate_transition
    flow=[
      "AVAILABLE","RESERVED","RELEASED","EMPTY_PICKUP","STUFFED","GATE_IN","LOADED",
      "IN_TRANSIT","TRANSSHIPMENT","DISCHARGED","GATE_OUT_FULL","EMPTY_RETURN","INSPECTION","AVAILABLE"
    ]
    for a,b in zip(flow,flow[1:]):
        assert validate_transition(a,b)
    assert validate_transition("ON_HIRE","AVAILABLE")
    assert validate_transition("OFF_HIRE_DUE","OFF_HIRED")
    assert validate_transition("RETURN_TO_PARTNER","PARTNER_RETURNED")
    assert validate_transition("RETURN_TO_AGENT","AGENT_RETURNED")
    assert validate_transition("ALLOCATED_FOR_SALE","SOLD")
    assert not validate_transition("AVAILABLE","LOADED")

def test_migration_is_additive_and_controls_present():
    sql=Path("migrations/CLX071_001_full_container_journey.sql").read_text()
    required=[
      "planned_time","actual_time","event_phase","voyage_ref","vessel_name",
      "detention_due_date","detention_rate","expected_empty_return","damage_hold","inspection_hold",
      "container_journey_exceptions","container_journey_policy","override_reason"
    ]
    for marker in required:
        assert marker in sql
    assert "DROP TABLE public.containers" not in sql

def test_native_m3_ui_has_journey_and_exception_control():
    html=Path("web/index.html").read_text()
    assert "Container Journey" in html
    assert "Journey Exceptions" in html
    assert "/api/clx071/journey/" in html
    assert "No separate ERP model or duplicate container inventory" in html

def test_manifest_keeps_production_locked():
    m=Path("CLX071_ACCEPTANCE_MANIFEST.json").read_text()
    assert '"production_traffic": "OFF"' in m
    assert '"live_providers": "OFF"' in m
    assert '"real_money": "OFF"' in m


def test_exception_decision_enforces_container_custody_scope():
    src=Path("app/clx071_container_journey.py").read_text()
    assert 'get_container(conn,e["container_no"],a)' in src


def test_detention_calculation_endpoint_is_same_clx071_engine():
    import app.main as main
    paths=set(main.app.openapi()["paths"])
    assert "/api/clx071/journey/detention/{record_id}/calculate" in paths


def test_detention_amount_reuses_mrg_rate_and_slabs():
    from app.clx071_container_journey import _detention_amount
    rule={"rule_ref":"R1","rate_basis":"PER_DAY","unit_rate":12,"minimum_rate":None,"maximum_rate":None,"slab_wise":0,"slabs":[]}
    amount,rate=_detention_amount(rule,5,"40HC",None)
    assert amount==60
    assert rate==12
    slab={"rule_ref":"R2","slab_wise":1,"slabs":[
      {"from_day":1,"till_day":3,"rate":10,"size_type":"40HC","container_type":None},
      {"from_day":4,"till_day":None,"rate":15,"size_type":"40HC","container_type":None},
    ]}
    amount,rate=_detention_amount(slab,5,"40HC",None)
    assert amount==60
    assert rate==12


def test_detention_process_frontend_binds_existing_screen_to_server_engine():
    html=Path("web/index.html").read_text()
    assert "function renderDetentionProcess" in html
    assert "function renderDetentionCollection" in html
    assert "runDetentionCalculation" in html
    assert "/api/clx071/journey/detention/" in html
    assert "Create Draft Invoice → Finance" in html
    assert "No GL posting occurs here" in html


def test_running_detention_advance_ongoing_actual_contract():
    import app.main as main
    paths=set(main.app.openapi()["paths"])
    assert "/api/clx071/journey/detention/{record_id}/calculate" in paths
    assert "/api/clx071/journey/detention/{record_id}/summary" in paths

    from app.clx071_container_journey import _detention_history_state
    h=[
      {"cumulative_days":5,"cumulative_amount":50,"covered_till":"2026-10-20T00:00:00+00:00"},
      {"cumulative_days":8,"cumulative_amount":80,"covered_till":"2026-10-23T00:00:00+00:00"},
    ]
    s=_detention_history_state(h)
    assert s["total_days"]==8
    assert s["cumulative_amount"]==80
    assert s["covered_till"].startswith("2026-10-23")


def test_detention_process_ui_has_running_advance_ongoing_actual_actions():
    html=Path("web/index.html").read_text()
    for marker in [
      "Calculate Advance","Recalculate Ongoing","Calculate Actual",
      "Previous Advance Till","Advance / Calculate Till","Ongoing Days",
      "Actual Chargeable Days","Total Chargeable Days",
      "Advance / Ongoing Period History","Finance / Collection · Read Only",
      "/api/clx071/journey/detention/"
    ]:
        assert marker in html
    assert "Create Draft Invoice → Finance" in html
    assert "Open Detention Collection" in html
    assert "Open Invoice / AR" in html
