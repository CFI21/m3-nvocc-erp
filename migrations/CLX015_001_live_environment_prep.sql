-- CLX-015 additive live-environment activation preparation only. No production traffic or live secrets.
CREATE TABLE IF NOT EXISTS clx015_activation_config (
 id INTEGER PRIMARY KEY CHECK(id=1), baseline TEXT NOT NULL, parent_baseline TEXT NOT NULL,
 environment TEXT NOT NULL, traffic_enabled INTEGER NOT NULL DEFAULT 0,
 live_credentials_enabled INTEGER NOT NULL DEFAULT 0, live_providers_enabled INTEGER NOT NULL DEFAULT 0,
 real_transactions_enabled INTEGER NOT NULL DEFAULT 0, real_money_enabled INTEGER NOT NULL DEFAULT 0,
 database_target TEXT NOT NULL, identity_target TEXT NOT NULL, storage_target TEXT NOT NULL,
 domain_tls_target TEXT NOT NULL, monitoring_target TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clx015_activation_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, item_key TEXT UNIQUE NOT NULL, category TEXT NOT NULL,
 status TEXT NOT NULL, required_for_activation INTEGER NOT NULL DEFAULT 1, owner_role TEXT NOT NULL,
 evidence TEXT NOT NULL, live_value_present INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clx015_cutover_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_ref TEXT UNIQUE NOT NULL, started_at TEXT NOT NULL,
 completed_at TEXT, status TEXT NOT NULL, evidence_json TEXT NOT NULL,
 production_traffic_opened INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clx015_cutover_steps (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_ref TEXT NOT NULL, step_no INTEGER NOT NULL,
 step_key TEXT NOT NULL, status TEXT NOT NULL, detail_json TEXT NOT NULL, UNIQUE(run_ref,step_no)
);
CREATE TABLE IF NOT EXISTS clx015_schema_versions (
 version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL, destructive INTEGER NOT NULL DEFAULT 0
);