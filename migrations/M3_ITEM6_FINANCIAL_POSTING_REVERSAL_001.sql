-- M3 Item 6 — Financial Posting + Reversal Governance
-- Additive governance metadata only. No duplicate ledger or finance model.
CREATE TABLE IF NOT EXISTS gl_period_override_approvals(
 id INTEGER PRIMARY KEY,
 approval_ref TEXT NOT NULL UNIQUE,
 period_id INTEGER NOT NULL REFERENCES gl_periods(id),
 voucher_id INTEGER NOT NULL REFERENCES gl_vouchers(id),
 action TEXT NOT NULL CHECK(action IN ('POST','REVERSE')),
 approval_role TEXT NOT NULL CHECK(approval_role IN ('FINANCE_MANAGER','CFO','EXTERNAL_ADVISOR','TS_BRANCH_MANAGER','TS_FINANCE')),
 approver_user_ref TEXT NOT NULL,
 approver_office_code TEXT,
 reason TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'APPROVED' CHECK(status IN ('APPROVED','REVOKED')),
 created_at TEXT NOT NULL,
 UNIQUE(period_id,voucher_id,action,approval_role)
);
