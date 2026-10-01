from pathlib import Path

def test_clx077_routes_mounted():
    import app.main as main
    paths=set(main.app.openapi()["paths"])
    required={
      "/api/clx077/dummy-bank/safety",
      "/api/clx077/dummy-bank/run",
      "/api/clx077/dummy-bank/dashboard",
      "/api/clx077/dummy-bank/reconcile",
      "/api/clx077/dummy-bank/archive-test-data",
    }
    assert required <= paths

def test_clx077_no_parallel_finance_model():
    sql=Path("migrations/CLX077_001_dummy_bank_live_review.sql").read_text()
    for forbidden in [
      "CREATE TABLE IF NOT EXISTS public.treasury_records",
      "CREATE TABLE IF NOT EXISTS public.bank_import_batches",
      "CREATE TABLE IF NOT EXISTS public.gl_vouchers",
      "CREATE TABLE IF NOT EXISTS public.sandbox_payment_requests",
      "CREATE TABLE IF NOT EXISTS public.provider_live_configs",
    ]:
        assert forbidden not in sql
    assert "m3-dummy-bank" in sql
    assert "DUMMY-EUR-CLX077" in sql
    assert "DUMMY-USD-CLX077" in sql

def test_clx077_safety_and_markers():
    src=Path("app/clx077_dummy_bank.py").read_text()
    assert 'M3_LIVE_PROVIDERS' in src
    assert 'REAL_MONEY' in src
    assert 'CLX077_TEST' in src
    assert 'DUMMY_BANK' in src
    assert 'real_money_movement":False' in src or '"real_money":False' in src
    assert "provider_live_events" in src
    assert "sandbox_payment_requests" in src
    assert "bank_import_lines" in src
    assert "gl_vouchers" in src

def test_clx077_has_15_scenarios_and_controls():
    src=Path("app/clx077_dummy_bank.py").read_text()
    for key in [
      "EXPORT_FCL_RECEIPT","IMPORT_FCL_RECEIPT","AGENT_COST_PAYMENT","CARRIER_COST_PAYMENT",
      "REPOSITION_COST","MR_COST","LEASE_SETTLEMENT","DETENTION_CHARGE","CREDIT_REFUND",
      "MULTI_CURRENCY","UNALLOCATED_MATCH","PAYMENT_REVERSAL","CUSTOMER_OVERPAYMENT",
      "PARTIAL_AP_PAYMENT","FINAL_JOB_RECON"
    ]:
        assert key in src
    assert "maximum_unexplained_variance" in src
    assert "unmatched_bank_items" in src
    assert "unbalanced_vouchers" in src

def test_clx077_ui_and_artifacts():
    html=Path("web/index.html").read_text()
    assert "Dummy Bank Review" in html
    assert "TEST BANK — SIMULATION ONLY — NO REAL MONEY" in html
    for p in [
      "CLX077_LIVE_SESSION_ACCEPTANCE.json","CLX077_DUMMY_BANK_RECONCILIATION.json",
      "CLX077_USER_REVIEW_CHECKLIST.md","CLX077_FINANCE_FLOW_UAT.json",
      "CLX077_TEST_DATA_REGISTER.json","CLX077_CLEANUP_RUNBOOK.md"
    ]:
        assert Path(p).exists(), p
