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
