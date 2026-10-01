from pathlib import Path

def test_clx073_router_is_mounted():
    import app.main as main
    paths=set(main.app.openapi()["paths"])
    required={
      "/api/clx073/optimization/policies",
      "/api/clx073/optimization/compare",
      "/api/clx073/optimization/runs/{run_ref}",
      "/api/clx073/optimization/runs/{run_ref}/decision",
      "/api/clx073/optimization/runs/{run_ref}/create-work-item",
      "/api/clx073/optimization/variance",
      "/api/clx073/optimization/exceptions",
      "/api/clx073/optimization/exceptions/{exception_ref}/decision",
      "/api/clx073/optimization/kpis",
    }
    assert required <= paths

def test_cost_scoring_prefers_lower_cost_and_lead():
    from app.clx073_equipment_optimization import score_option
    assert score_option(1000,3,1) < score_option(1200,2,1)
    assert score_option(1000,2,1) < score_option(1000,5,1)

def test_migration_does_not_add_parallel_rate_or_inventory_model():
    sql=Path("migrations/CLX073_001_equipment_cost_optimization.sql").read_text()
    assert "equipment_optimization_runs" in sql
    assert "equipment_optimization_options" in sql
    assert "equipment_execution_variance" in sql
    assert "equipment_optimization_exceptions" in sql
    assert "CREATE TABLE IF NOT EXISTS public.containers" not in sql
    assert "CREATE TABLE IF NOT EXISTS public.equipment_work_items" not in sql
    assert "rate master" in sql.lower()

def test_manifest_keeps_production_locked_and_rate_model_single():
    m=Path("CLX073_ACCEPTANCE_MANIFEST.json").read_text()
    assert '"production_traffic": "OFF"' in m
    assert '"live_providers": "OFF"' in m
    assert '"real_money": "OFF"' in m
    assert '"duplicate_rate_model": false' in m
    assert '"duplicate_container_inventory": false' in m

def test_native_ui_has_cost_optimization():
    html=Path("web/index.html").read_text()
    assert "Cost Optimization" in html
    assert "/api/clx073/optimization/compare" in html
    assert "Planned vs Actual" in html
    assert "No separate ERP model or duplicate container inventory" in html


def test_exception_insert_placeholder_count_and_scope_guards():
    src=Path("app/clx073_equipment_optimization.py").read_text()
    assert "VALUES(?,?,?,?,?,?,'OPEN',?,?,?,?)" in src
    assert "def work_item_in_scope" in src
    assert "require_run_scope(c,a,r)" in src
    assert "Optimization exception not found in actor scope" in src
