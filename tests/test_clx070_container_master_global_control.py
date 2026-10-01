from pathlib import Path

API=Path("app/clx070_equipment_runtime.py").read_text()
SCHEMA=Path("app/schema.sql").read_text()
MIG=Path("migrations/CLX070_002_container_master_global_control.sql").read_text()
WEB=Path("web/index.html").read_text()

def test_one_authoritative_container_master_extended_not_duplicated():
    assert "ALTER TABLE public.containers ADD COLUMN IF NOT EXISTS owner_type" in MIG
    assert "CREATE TABLE IF NOT EXISTS public.container_party_links" in MIG
    assert "CREATE TABLE IF NOT EXISTS public.container_financial_entries" in MIG
    assert "CREATE TABLE IF NOT EXISTS public.container_share_rules" in MIG
    assert "CREATE TABLE IF NOT EXISTS public.equipment_policy_rules" in MIG
    assert "CREATE TABLE IF NOT EXISTS container_financial_entries" in SCHEMA

def test_owner_provider_types_and_custody_fields_present():
    for marker in [
        "PRINCIPAL","OVERSEAS_PARTNER","LEASING_COMPANY","INVESTOR","AGENT_SUPPLIED","SOC",
        "principal_owner_code","overseas_partner_code","leasing_company_code","investor_code",
        "agent_supplier_code","branch_code","agent_code","depot_code"
    ]:
        assert marker in API or marker in MIG

def test_container_finance_and_profitability_contract_present():
    for marker in [
        "FINANCE_CATEGORIES","REVENUE","COST","COMMISSION","SHARE",
        "finance_summary","CONTAINER_FINANCE_CREATE","CONTAINER_SHARE_RULE_CREATE",
        "movement_event_ref","MOVE_COST"
    ]:
        assert marker in API

def test_system_policy_role_scope_model_present():
    for marker in ["require_policy","equipment_policy_rules","X-Agent-Scope","x_branch_scope","four_eyes"]:
        assert marker in API or marker in MIG
    assert "System Policy" in WEB
    assert "Operational Scope" in WEB

def test_booking_and_container_master_are_interlinked_in_ui():
    assert "const CLX49_BOOKING_TABS=['Booking Info','Equipment','Other Info']" in WEB
    assert "openBookingEquipmentControl" in WEB
    assert "same Booking/Job context" in WEB
    assert "openEquipmentContainer" in WEB

def test_no_new_standalone_equipment_erp_link():
    assert "window.open('/equipment-runtime.html'" not in WEB
    assert "Native M3 workspace" not in WEB or "One M3 ERP." in WEB
