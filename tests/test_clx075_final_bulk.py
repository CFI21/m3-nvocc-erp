from pathlib import Path

def test_clx075_routes_and_inherited_flow_are_mounted():
    import app.main as main
    paths=set(main.app.openapi()["paths"])
    required={
      "/api/clx049/workspace/{job_ref}",
      "/api/clx070/container-control/containers",
      "/api/clx071/journey/containers/{container_no}/events",
      "/api/clx072/network/position",
      "/api/clx073/optimization/compare",
      "/api/clx074/mr/kpis",
      "/api/clx075/lease/contracts",
      "/api/clx075/lease/contracts/{contract_ref}/decision",
      "/api/clx075/lease/contracts/{contract_ref}/on-hire",
      "/api/clx075/lease/allocations/{allocation_ref}/accrue",
      "/api/clx075/lease/allocations/{allocation_ref}/off-hire",
      "/api/clx075/lease/allocations/{allocation_ref}/settle",
      "/api/clx075/lease/exceptions",
      "/api/clx075/readiness/matrix",
      "/api/clx075/readiness/data-reconciliation",
      "/api/clx075/readiness/security",
      "/api/clx075/readiness/cutover/rehearse",
      "/api/clx075/readiness/final-gate",
    }
    assert required <= paths

def test_lease_accrual_honors_minimum_hire_free_days_and_fixed_costs():
    from app.clx075_lease import accrued
    r={
      "on_hire_date":"2026-10-01T00:00:00+00:00",
      "minimum_hire_days":10,"free_days":2,"per_diem_rate":5,
      "handling_rate":10,"depot_rate":3,"lift_on_rate":4,"pick_up_rate":2,
    }
    amount,billable,elapsed=accrued(r,"2026-10-05T00:00:00+00:00")
    assert elapsed==5
    assert billable==10
    assert amount==69

def test_clx075_migration_is_additive_and_reuses_authoritative_models():
    sql=Path("migrations/CLX075_001_final_bulk_readiness.sql").read_text()
    assert "equipment_lease_contracts" in sql
    assert "equipment_lease_allocations" in sql
    assert "clx075_cutover_rehearsals" in sql
    assert "CREATE TABLE IF NOT EXISTS public.containers" not in sql
    assert "CREATE TABLE IF NOT EXISTS public.container_financial_ledger" not in sql
    assert "CREATE TABLE IF NOT EXISTS public.equipment_work_items" not in sql
    assert "CREATE TABLE IF NOT EXISTS public.bookings" not in sql
    assert "CREATE TABLE IF NOT EXISTS public.jobs" not in sql

def test_lease_finance_reuses_existing_container_ledger():
    src=Path("app/clx075_lease.py").read_text()
    assert "INSERT INTO container_financial_ledger" in src
    assert "lease_contract_ref" in src
    assert "MAKER_CHECKER_CONFLICT" in src
    assert "OFFHIRE_INSPECTION_REQUIRED" in src

def test_final_artifacts_and_hard_stops_exist():
    required=[
      "CLX075_FINAL_ACCEPTANCE_MANIFEST.json",
      "CLX075_PRODUCTION_READINESS_MANIFEST.json",
      "CLX075_CUTOVER_RUNBOOK.md",
      "CLX075_ROLLBACK_RUNBOOK.md",
      "CLX075_DATA_MIGRATION_RECONCILIATION.json",
      "CLX075_UAT_RESULTS.json",
      "CLX075_SECURITY_READINESS.json",
      "CLX075_OPERATIONS_RUNBOOK.md",
      "CLX075_FINAL_GAP_MATRIX.json",
    ]
    for p in required: assert Path(p).exists(), p
    import json
    m=json.loads(Path("CLX075_FINAL_ACCEPTANCE_MANIFEST.json").read_text())
    assert m["production_traffic"]=="OFF"
    assert m["live_providers"]=="OFF"
    assert m["real_money"]=="OFF"
    assert m["architecture"]["duplicate_models"] is False
    assert m["architecture"]["new_erp_shell"] is False

def test_ui_preserves_shell_and_adds_lease_control():
    html=Path("web/index.html").read_text()
    assert "Lease Control" in html
    assert "/api/clx075/lease/kpis" in html
    assert "Depot &amp; M&amp;R" in html
    assert "Cost Optimization" in html

def test_final_readiness_has_activation_hard_stop():
    src=Path("app/clx075_readiness.py").read_text()
    assert '"production_traffic":"OFF"' in src
    assert '"live_providers":"OFF"' in src
    assert '"real_money":"OFF"' in src
    assert "activation_authorized" in src
    assert "AUTHORIZE CONTROLLED PRODUCTION ACTIVATION" in src
