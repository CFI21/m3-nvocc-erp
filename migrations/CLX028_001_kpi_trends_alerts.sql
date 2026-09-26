-- CLX-028 KPI Trends + Daily Operations Snapshot + Management Alerts
CREATE TABLE IF NOT EXISTS management_daily_snapshots (
  snapshot_date TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  scope_key TEXT NOT NULL DEFAULT 'GLOBAL',
  jobs_total INTEGER NOT NULL,
  jobs_open INTEGER NOT NULL,
  jobs_closed INTEGER NOT NULL,
  release_blocked INTEGER NOT NULL,
  document_gaps INTEGER NOT NULL,
  payment_blocks INTEGER NOT NULL,
  outstanding_total NUMERIC NOT NULL,
  active_work_items INTEGER NOT NULL,
  critical INTEGER NOT NULL,
  high INTEGER NOT NULL,
  overdue INTEGER NOT NULL,
  unassigned INTEGER NOT NULL,
  team_workload_json TEXT NOT NULL,
  exception_categories_json TEXT NOT NULL,
  priority_json TEXT NOT NULL,
  risk_jobs_json TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  immutable INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS management_alert_rules (
  rule_key TEXT PRIMARY KEY,
  metric_key TEXT NOT NULL,
  operator TEXT NOT NULL,
  threshold_value NUMERIC NOT NULL,
  severity TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  team TEXT,
  description TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (operator IN ('GT','GTE','LT','LTE','EQ')),
  CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW'))
);

CREATE TABLE IF NOT EXISTS management_alert_events (
  alert_key TEXT PRIMARY KEY,
  snapshot_date TEXT NOT NULL,
  rule_key TEXT NOT NULL,
  metric_key TEXT NOT NULL,
  metric_value NUMERIC NOT NULL,
  threshold_value NUMERIC NOT NULL,
  severity TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  acknowledged_by TEXT,
  acknowledged_at TEXT,
  closed_at TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  CHECK (status IN ('OPEN','ACKNOWLEDGED','CLOSED'))
);

CREATE INDEX IF NOT EXISTS ix_management_alert_events_status ON management_alert_events(status,severity);
CREATE INDEX IF NOT EXISTS ix_management_alert_events_date ON management_alert_events(snapshot_date);
