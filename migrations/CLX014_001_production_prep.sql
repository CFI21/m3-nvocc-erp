-- CLX-014 additive production infrastructure preparation only.
CREATE TABLE IF NOT EXISTS clx014_prod_config (
 id INTEGER PRIMARY KEY CHECK(id=1), environment TEXT NOT NULL, database_mode TEXT NOT NULL,
 credential_state TEXT NOT NULL, provider_state TEXT NOT NULL, traffic_enabled INTEGER NOT NULL DEFAULT 0,
 oidc_state TEXT NOT NULL, object_storage_state TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clx014_cert_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_ref TEXT UNIQUE NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
 status TEXT NOT NULL, evidence_json TEXT NOT NULL, production_promoted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clx014_cutover_steps (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_ref TEXT NOT NULL, step_no INTEGER NOT NULL, step_key TEXT NOT NULL,
 status TEXT NOT NULL, detail_json TEXT NOT NULL, UNIQUE(run_ref,step_no)
);
CREATE TABLE IF NOT EXISTS clx014_handover_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, item_key TEXT UNIQUE NOT NULL, status TEXT NOT NULL, owner_role TEXT NOT NULL, evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clx014_security_findings (
 id INTEGER PRIMARY KEY AUTOINCREMENT, finding_ref TEXT UNIQUE NOT NULL, severity TEXT NOT NULL, category TEXT NOT NULL,
 status TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clx014_schema_versions (
 version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL, destructive INTEGER NOT NULL DEFAULT 0
);