-- CLX-026 Exception Workflow + Ownership Automation
-- Extends CLX-025 work-item state only; source business tables remain unchanged.
ALTER TABLE operations_work_items DROP CONSTRAINT IF EXISTS operations_work_items_work_status_check;
ALTER TABLE operations_work_items
  ADD COLUMN IF NOT EXISTS team TEXT,
  ADD COLUMN IF NOT EXISTS category TEXT,
  ADD COLUMN IF NOT EXISTS job_id INTEGER,
  ADD COLUMN IF NOT EXISTS job_ref TEXT,
  ADD COLUMN IF NOT EXISTS sla_due_at TEXT,
  ADD COLUMN IF NOT EXISTS escalation_state TEXT NOT NULL DEFAULT 'NONE',
  ADD COLUMN IF NOT EXISTS resolution_code TEXT,
  ADD COLUMN IF NOT EXISTS resolved_by TEXT,
  ADD COLUMN IF NOT EXISTS resolved_at TEXT,
  ADD COLUMN IF NOT EXISTS last_seen_at TEXT,
  ADD COLUMN IF NOT EXISTS source_active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE operations_work_items
  ADD CONSTRAINT operations_work_items_work_status_check
  CHECK (work_status IN ('OPEN','ACKNOWLEDGED','IN_PROGRESS','RESOLVED','CLOSED'));
ALTER TABLE operations_work_items DROP CONSTRAINT IF EXISTS operations_work_items_escalation_state_check;
ALTER TABLE operations_work_items
  ADD CONSTRAINT operations_work_items_escalation_state_check
  CHECK (escalation_state IN ('NONE','DUE_SOON','OVERDUE','ESCALATED'));
CREATE INDEX IF NOT EXISTS ix_operations_work_items_team ON operations_work_items(team);
CREATE INDEX IF NOT EXISTS ix_operations_work_items_due ON operations_work_items(sla_due_at);
CREATE INDEX IF NOT EXISTS ix_operations_work_items_active ON operations_work_items(source_active,work_status);

CREATE TABLE IF NOT EXISTS operations_work_history (
  id BIGSERIAL PRIMARY KEY,
  exception_key TEXT NOT NULL,
  ts TEXT NOT NULL,
  actor_role TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  action TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  owner TEXT,
  team TEXT,
  resolution_code TEXT,
  comment TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_operations_work_history_key ON operations_work_history(exception_key,id);
CREATE INDEX IF NOT EXISTS ix_operations_work_history_ts ON operations_work_history(ts);
