from pathlib import Path

def test_clx074_routes_mounted():
    import app.main as main
    paths=set(main.app.openapi()["paths"])
    required={
      "/api/clx074/mr/policies",
      "/api/clx074/mr/containers/{container_no}/inspection",
      "/api/clx074/mr/inspections/{inspection_ref}/damage",
      "/api/clx074/mr/inspections/{inspection_ref}/documents",
      "/api/clx074/mr/inspections/{inspection_ref}/estimate",
      "/api/clx074/mr/estimates/{estimate_ref}/decision",
      "/api/clx074/mr/estimates/{estimate_ref}/repair-order",
      "/api/clx074/mr/repair-orders/{work_ref}/complete",
      "/api/clx074/mr/repair-orders/{work_ref}/reinspect",
      "/api/clx074/mr/damage/{damage_ref}/liability",
      "/api/clx074/mr/scan-exceptions",
      "/api/clx074/mr/exceptions",
      "/api/clx074/mr/exceptions/{exception_ref}/decision",
      "/api/clx074/mr/kpis"
    }
    assert required <= paths

def test_no_parallel_container_or_finance_model():
    sql=Path("migrations/CLX074_001_depot_inspection_mr.sql").read_text()
    assert "container_inspections" in sql and "container_repair_orders" in sql
    assert "CREATE TABLE IF NOT EXISTS public.containers" not in sql
    assert "CREATE TABLE IF NOT EXISTS public.container_financial_ledger" not in sql
    assert "CREATE TABLE IF NOT EXISTS public.equipment_work_items" not in sql

def test_repair_flow_reuses_authoritative_models():
    repair=Path("app/clx074_repair.py").read_text()
    inspection=Path("app/clx074_inspection.py").read_text()
    assert "INSERT INTO equipment_work_items" in repair
    assert "INSERT INTO container_financial_ledger" in repair
    assert "INSERT INTO container_documents" in inspection
    assert "get_container" in repair and "get_container" in inspection

def test_reinspection_controls_available_state():
    repair=Path("app/clx074_repair.py").read_text()
    assert "equipment_status='AVAILABLE'" in repair
    assert "equipment_status='HOLD'" in repair
    assert "REINSPECTION_FAILURE" in repair

def test_manifest_and_ui_safety():
    m=Path("CLX074_ACCEPTANCE_MANIFEST.json").read_text()
    html=Path("web/index.html").read_text()
    assert '"production_traffic":"OFF"' in m
    assert '"duplicate_container_model":false' in m
    assert '"duplicate_finance_model":false' in m
    assert "Depot &amp; M&amp;R" in html
    assert "/api/clx074/mr/kpis" in html
