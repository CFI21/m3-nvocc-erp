-- CLX-013 additive pre-production readiness schema only.
-- No DROP/ALTER/destructive migration is permitted in this phase.
CREATE TABLE IF NOT EXISTS clx013_schema_versions(
  version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL, destructive INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clx013_request_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE,
  correlation_id TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL, status_code INTEGER NOT NULL,
  duration_ms REAL NOT NULL, actor_role TEXT, office TEXT, job_ref TEXT, error_class TEXT
);
CREATE TABLE IF NOT EXISTS clx013_backup_manifests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, backup_ref TEXT NOT NULL UNIQUE, file_name TEXT NOT NULL,
  sha256 TEXT NOT NULL, source_db TEXT NOT NULL, size_bytes INTEGER NOT NULL, integrity_status TEXT NOT NULL,
  created_at TEXT NOT NULL, restore_status TEXT, restore_duration_ms REAL, restore_evidence_json TEXT
);
CREATE TABLE IF NOT EXISTS clx013_deployment_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_ref TEXT NOT NULL UNIQUE, environment TEXT NOT NULL,
  status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, gate_json TEXT NOT NULL,
  smoke_json TEXT, rollback_json TEXT
);
CREATE TABLE IF NOT EXISTS clx013_runtime_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_ref TEXT NOT NULL UNIQUE, ts TEXT NOT NULL,
  category TEXT NOT NULL, severity TEXT NOT NULL, action TEXT NOT NULL, detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clx013_runtime_mode(
  id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL, reason TEXT, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS clx013_request_log_no_update BEFORE UPDATE ON clx013_request_log BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CLX013_REQUEST_LOG'); END;
CREATE TRIGGER IF NOT EXISTS clx013_request_log_no_delete BEFORE DELETE ON clx013_request_log BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CLX013_REQUEST_LOG'); END;
CREATE TRIGGER IF NOT EXISTS clx013_runtime_events_no_update BEFORE UPDATE ON clx013_runtime_events BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CLX013_EVENT'); END;
CREATE TRIGGER IF NOT EXISTS clx013_runtime_events_no_delete BEFORE DELETE ON clx013_runtime_events BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CLX013_EVENT'); END;