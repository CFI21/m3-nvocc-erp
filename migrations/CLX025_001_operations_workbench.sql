-- CLX-025 Operations Workbench state only.
-- Existing operational/financial source tables are unchanged.
CREATE TABLE IF NOT EXISTS operations_work_items (
  exception_key TEXT PRIMARY KEY,
  owner TEXT,
  work_status TEXT NOT NULL DEFAULT 'OPEN',
  priority_override TEXT,
  acknowledged_by TEXT,
  acknowledged_at TEXT,
  note TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (work_status IN ('OPEN','ACKNOWLEDGED','IN_PROGRESS','CLOSED')),
  CHECK (priority_override IS NULL OR priority_override IN ('CRITICAL','HIGH','MEDIUM','LOW'))
);
CREATE INDEX IF NOT EXISTS ix_operations_work_items_owner ON operations_work_items(owner);
CREATE INDEX IF NOT EXISTS ix_operations_work_items_status ON operations_work_items(work_status);
