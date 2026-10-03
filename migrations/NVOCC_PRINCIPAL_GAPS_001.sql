-- M3 NVOCC PRINCIPAL GAP COMPLETION — ADDITIVE ONLY
-- Preserves existing Booking/Job/B-L/Container/Finance models and the 196-screen baseline.
CREATE TABLE IF NOT EXISTS nvocc_branch_profiles(
 branch_code TEXT PRIMARY KEY,branch_type TEXT NOT NULL,legal_entity_code TEXT NOT NULL,country_code TEXT,city TEXT,ports_served TEXT,port_roles TEXT,
 cost_center TEXT,profit_center TEXT,default_depot_code TEXT,default_carrier_depot_code TEXT,contact TEXT,sla TEXT,active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_legal_entity_profiles(
 entity_code TEXT PRIMARY KEY,registration_no TEXT,tax_id TEXT,currency TEXT NOT NULL,consolidation_group TEXT,intercompany_flag INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_depots(
 depot_code TEXT PRIMARY KEY,depot_name TEXT NOT NULL,depot_type TEXT NOT NULL,ownership TEXT NOT NULL,legal_entity_code TEXT,vendor_code TEXT,carrier_code TEXT,branch_code TEXT,
 port_code TEXT NOT NULL,address TEXT NOT NULL,gps_coordinates TEXT,capacity_teu REAL,free_time_days REAL NOT NULL DEFAULT 0,storage_rate REAL NOT NULL DEFAULT 0,handling_rate REAL,
 mr_capability INTEGER NOT NULL DEFAULT 0,reefer_plugs INTEGER,dangerous_goods INTEGER NOT NULL DEFAULT 0,customs_bonded INTEGER NOT NULL DEFAULT 0,operating_hours TEXT,contact TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_port_agent_depot_config(
 config_id TEXT PRIMARY KEY,port_code TEXT NOT NULL,port_role TEXT NOT NULL,agent_code TEXT NOT NULL,agent_type TEXT NOT NULL,depot_code TEXT,depot_type TEXT,
 empty_return_depot TEXT,carrier_depot TEXT,handling_instructions TEXT,cost_allocation TEXT NOT NULL,effective_from TEXT NOT NULL,effective_to TEXT,priority INTEGER NOT NULL DEFAULT 1,
 active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_transfer_pricing(
 rule_id TEXT PRIMARY KEY,from_branch_type TEXT NOT NULL,to_branch_type TEXT NOT NULL,service_type TEXT NOT NULL,pricing_method TEXT NOT NULL,markup_pct REAL,fixed_amount REAL,
 currency TEXT NOT NULL,effective_from TEXT NOT NULL,effective_to TEXT,active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_ts_branch_operations(
 task_ref TEXT PRIMARY KEY,job_ref TEXT NOT NULL,container_no TEXT NOT NULL,ts_port TEXT NOT NULL,ts_branch TEXT NOT NULL,branch_manager TEXT,cost_center TEXT,profit_center TEXT,
 legal_entity_code TEXT,scope TEXT,internal_sla TEXT,backup_branch TEXT,from_branch TEXT,to_branch TEXT,mbl_no TEXT,hbl_no TEXT,connecting_vessel TEXT,connecting_voyage TEXT,
 eta_ts TEXT,etd_ts TEXT,free_time REAL,special_instructions TEXT,documents_required TEXT,due_date TEXT,priority TEXT,acknowledged_by TEXT,acknowledged_at TEXT,accepted_scope TEXT,
 exceptions_noted TEXT,estimated_internal_cost REAL,resource_assigned TEXT,discharge_at TEXT,condition TEXT,damage_details TEXT,yard_location TEXT,storage_start TEXT,free_time_end TEXT,
 storage_rate REAL,storage_days REAL,storage_cost REAL,reload_at TEXT,stowage_position TEXT,departure_at TEXT,eta_final_port TEXT,branch_reference TEXT,documents_sent TEXT,
 cost_lines_json TEXT,documents_json TEXT,exceptions_json TEXT,status TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_release_controls(
 release_ref TEXT PRIMARY KEY,job_ref TEXT NOT NULL,hbl_no TEXT NOT NULL,pod_agent TEXT,pod_agent_type TEXT,pod_depot TEXT,pod_depot_type TEXT,empty_return_depot TEXT,
 empty_return_depot_type TEXT,release_date TEXT,release_by TEXT,status TEXT NOT NULL DEFAULT 'Pending',dd_start TEXT,cost_allocation TEXT,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_interbranch_settlements(
 settlement_ref TEXT PRIMARY KEY,from_branch TEXT NOT NULL,to_branch TEXT NOT NULL,legal_entity_from TEXT,legal_entity_to TEXT,job_ref TEXT NOT NULL,container_no TEXT,
 service_period_from TEXT,service_period_to TEXT,charges_json TEXT,total_amount REAL NOT NULL DEFAULT 0,currency TEXT NOT NULL,exchange_rate REAL,status TEXT NOT NULL,
 gl_posting_ref TEXT,elimination_flag INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nvocc_extension_audit(
 audit_ref TEXT PRIMARY KEY,ts TEXT NOT NULL,actor_role TEXT NOT NULL,branch_scope TEXT,workspace_key TEXT NOT NULL,record_ref TEXT NOT NULL,action TEXT NOT NULL,
 before_json TEXT,after_json TEXT);


-- Supabase public-schema safety: these tables are server-side M3 ERP internals.
-- They are not exposed to browser/Data API roles.
ALTER TABLE public.nvocc_branch_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nvocc_legal_entity_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nvocc_depots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nvocc_port_agent_depot_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nvocc_transfer_pricing ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nvocc_ts_branch_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nvocc_release_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nvocc_interbranch_settlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nvocc_extension_audit ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.nvocc_branch_profiles FROM anon, authenticated;
REVOKE ALL ON TABLE public.nvocc_legal_entity_profiles FROM anon, authenticated;
REVOKE ALL ON TABLE public.nvocc_depots FROM anon, authenticated;
REVOKE ALL ON TABLE public.nvocc_port_agent_depot_config FROM anon, authenticated;
REVOKE ALL ON TABLE public.nvocc_transfer_pricing FROM anon, authenticated;
REVOKE ALL ON TABLE public.nvocc_ts_branch_operations FROM anon, authenticated;
REVOKE ALL ON TABLE public.nvocc_release_controls FROM anon, authenticated;
REVOKE ALL ON TABLE public.nvocc_interbranch_settlements FROM anon, authenticated;
REVOKE ALL ON TABLE public.nvocc_extension_audit FROM anon, authenticated;
