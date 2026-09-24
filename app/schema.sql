PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents(id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vessels(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS voyages(id INTEGER PRIMARY KEY, voyage_no TEXT NOT NULL UNIQUE, vessel_id INTEGER NOT NULL REFERENCES vessels(id));
CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY, booking_ref TEXT NOT NULL UNIQUE, customer_id INTEGER NOT NULL REFERENCES customers(id), agent_id INTEGER NOT NULL REFERENCES agents(id), voyage_id INTEGER NOT NULL REFERENCES voyages(id), pol TEXT NOT NULL, pod TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, job_ref TEXT NOT NULL UNIQUE CHECK(length(job_ref)=5), booking_id INTEGER NOT NULL UNIQUE REFERENCES bookings(id), customer_id INTEGER NOT NULL REFERENCES customers(id), agent_id INTEGER NOT NULL REFERENCES agents(id), voyage_id INTEGER NOT NULL REFERENCES voyages(id), pol TEXT NOT NULL, pod TEXT NOT NULL, operational_status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS containers(id INTEGER PRIMARY KEY, container_no TEXT NOT NULL UNIQUE, job_id INTEGER NOT NULL REFERENCES jobs(id), size_type TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bills(id INTEGER PRIMARY KEY, bill_no TEXT NOT NULL UNIQUE, job_id INTEGER NOT NULL REFERENCES jobs(id), kind TEXT NOT NULL CHECK(kind IN ('HBL','MBL')), status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_states(id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id), payment_status TEXT NOT NULL, currency TEXT NOT NULL, outstanding REAL NOT NULL DEFAULT 0, credit_hold INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS workflow_states(id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id), documentation_status TEXT NOT NULL, vgm_status TEXT NOT NULL, customs_status TEXT NOT NULL, transshipment_status TEXT NOT NULL, release_status TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS workflow_holds(id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id), code TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, cleared_at TEXT, UNIQUE(job_id,code));
CREATE TABLE IF NOT EXISTS transaction_records(id INTEGER PRIMARY KEY, module TEXT NOT NULL, external_ref TEXT NOT NULL, job_id INTEGER NOT NULL REFERENCES jobs(id), booking_id INTEGER NOT NULL REFERENCES bookings(id), customer_id INTEGER NOT NULL REFERENCES customers(id), agent_id INTEGER NOT NULL REFERENCES agents(id), container_id INTEGER REFERENCES containers(id), voyage_id INTEGER NOT NULL REFERENCES voyages(id), bill_id INTEGER REFERENCES bills(id), status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(module,external_ref));
CREATE INDEX IF NOT EXISTS idx_tx_module_job ON transaction_records(module,job_id);
CREATE INDEX IF NOT EXISTS idx_tx_agent ON transaction_records(agent_id);
CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, ts TEXT NOT NULL, actor_role TEXT NOT NULL, actor_scope TEXT, action TEXT NOT NULL, module TEXT, transaction_id INTEGER, job_id INTEGER REFERENCES jobs(id), before_json TEXT, after_json TEXT, metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exception_events(id INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, ts TEXT NOT NULL, code TEXT NOT NULL, severity TEXT NOT NULL, module TEXT, transaction_id INTEGER, job_id INTEGER REFERENCES jobs(id), detail TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS idempotency_keys(id INTEGER PRIMARY KEY, actor_role TEXT NOT NULL, idem_key TEXT NOT NULL, request_hash TEXT NOT NULL, response_json TEXT NOT NULL, status_code INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(actor_role,idem_key));

CREATE TABLE IF NOT EXISTS special_rate_workflow(id INTEGER PRIMARY KEY, transaction_id INTEGER NOT NULL UNIQUE REFERENCES transaction_records(id) ON DELETE CASCADE, stage TEXT NOT NULL, carrier_response TEXT, approved_rate REAL, quote_ref TEXT, booking_ref TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS switch_bl_history(id INTEGER PRIMARY KEY, transaction_id INTEGER NOT NULL REFERENCES transaction_records(id) ON DELETE CASCADE, job_id INTEGER NOT NULL REFERENCES jobs(id), ts TEXT NOT NULL, original_bill_no TEXT NOT NULL, switch_bill_no TEXT NOT NULL, original_parties_json TEXT NOT NULL, new_parties_json TEXT NOT NULL, approved_by TEXT, confidentiality INTEGER NOT NULL DEFAULT 1, immutable_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS split_bl_allocations(id INTEGER PRIMARY KEY, transaction_id INTEGER NOT NULL REFERENCES transaction_records(id) ON DELETE CASCADE, child_bill_no TEXT NOT NULL, container_no TEXT NOT NULL, packages REAL NOT NULL, weight REAL NOT NULL, measurement REAL NOT NULL, UNIQUE(transaction_id,child_bill_no));
CREATE TABLE IF NOT EXISTS container_events(id INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, job_id INTEGER NOT NULL REFERENCES jobs(id), container_id INTEGER NOT NULL REFERENCES containers(id), event_type TEXT NOT NULL, event_time TEXT NOT NULL, location TEXT NOT NULL, status TEXT NOT NULL, source_module TEXT NOT NULL, detail_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_container_events_job ON container_events(job_id,event_time);


-- CLX-005 GL / ACCOUNTS FOUNDATION
CREATE TABLE IF NOT EXISTS gl_accounts(
 id INTEGER PRIMARY KEY, account_code TEXT NOT NULL UNIQUE, account_name TEXT NOT NULL,
 account_type TEXT NOT NULL, parent_code TEXT, currency TEXT NOT NULL DEFAULT 'USD', active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gl_currencies(
 code TEXT PRIMARY KEY, name TEXT NOT NULL, decimals INTEGER NOT NULL DEFAULT 2, rate_type TEXT NOT NULL DEFAULT 'SPOT', active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gl_sequences(
 voucher_type TEXT PRIMARY KEY, prefix TEXT NOT NULL, next_number INTEGER NOT NULL DEFAULT 1, approval_required INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gl_account_mappings(
 id INTEGER PRIMARY KEY, source_module TEXT NOT NULL, event_type TEXT NOT NULL,
 debit_account_code TEXT NOT NULL REFERENCES gl_accounts(account_code), credit_account_code TEXT NOT NULL REFERENCES gl_accounts(account_code),
 tax_account_code TEXT REFERENCES gl_accounts(account_code), active INTEGER NOT NULL DEFAULT 1,
 UNIQUE(source_module,event_type)
);
CREATE TABLE IF NOT EXISTS gl_records(
 id INTEGER PRIMARY KEY, module TEXT NOT NULL, external_ref TEXT NOT NULL, job_id INTEGER REFERENCES jobs(id),
 source_type TEXT, source_ref TEXT, status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(module,external_ref)
);
CREATE INDEX IF NOT EXISTS idx_gl_records_module_job ON gl_records(module,job_id);
CREATE TABLE IF NOT EXISTS gl_vouchers(
 id INTEGER PRIMARY KEY, gl_record_id INTEGER REFERENCES gl_records(id), voucher_no TEXT NOT NULL UNIQUE, voucher_type TEXT NOT NULL,
 voucher_date TEXT NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL, source_type TEXT, source_ref TEXT,
 job_id INTEGER REFERENCES jobs(id), total_debit REAL NOT NULL DEFAULT 0, total_credit REAL NOT NULL DEFAULT 0,
 version INTEGER NOT NULL DEFAULT 1, reversal_of INTEGER REFERENCES gl_vouchers(id), approved_by TEXT, approved_at TEXT, posted_at TEXT, created_at TEXT NOT NULL,
 maker_role TEXT, exchange_rate REAL NOT NULL DEFAULT 1, base_currency TEXT NOT NULL DEFAULT 'USD', base_total_debit REAL NOT NULL DEFAULT 0, base_total_credit REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gl_voucher_lines(
 id INTEGER PRIMARY KEY, voucher_id INTEGER NOT NULL REFERENCES gl_vouchers(id) ON DELETE CASCADE, line_no INTEGER NOT NULL,
 account_code TEXT NOT NULL REFERENCES gl_accounts(account_code), debit REAL NOT NULL DEFAULT 0, credit REAL NOT NULL DEFAULT 0,
 description TEXT, job_id INTEGER REFERENCES jobs(id), UNIQUE(voucher_id,line_no), CHECK(debit>=0 AND credit>=0), CHECK(NOT(debit>0 AND credit>0))
);
CREATE TABLE IF NOT EXISTS gl_source_links(
 id INTEGER PRIMARY KEY, gl_record_id INTEGER REFERENCES gl_records(id) ON DELETE CASCADE, voucher_id INTEGER REFERENCES gl_vouchers(id) ON DELETE CASCADE,
 source_type TEXT NOT NULL, source_ref TEXT NOT NULL, job_id INTEGER REFERENCES jobs(id), UNIQUE(gl_record_id,source_type,source_ref)
);
CREATE TABLE IF NOT EXISTS gl_reconciliation_items(
 id INTEGER PRIMARY KEY, gl_record_id INTEGER NOT NULL REFERENCES gl_records(id) ON DELETE CASCADE,
 bank_account_code TEXT NOT NULL REFERENCES gl_accounts(account_code), statement_ref TEXT NOT NULL, book_amount REAL NOT NULL, bank_amount REAL NOT NULL,
 difference REAL NOT NULL, reconciled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gl_cheque_books(
 id INTEGER PRIMARY KEY, book_no TEXT NOT NULL UNIQUE, bank_account_code TEXT NOT NULL REFERENCES gl_accounts(account_code),
 start_no INTEGER NOT NULL, end_no INTEGER NOT NULL, next_no INTEGER NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gl_budget_lines(
 id INTEGER PRIMARY KEY, fiscal_year INTEGER NOT NULL, period INTEGER NOT NULL, account_code TEXT NOT NULL REFERENCES gl_accounts(account_code),
 amount REAL NOT NULL, UNIQUE(fiscal_year,period,account_code)
);
CREATE TRIGGER IF NOT EXISTS gl_audit_immutable_update BEFORE UPDATE ON audit_events WHEN OLD.module LIKE 'gl:%'
BEGIN SELECT RAISE(ABORT,'GL_AUDIT_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS gl_audit_immutable_delete BEFORE DELETE ON audit_events WHEN OLD.module LIKE 'gl:%'
BEGIN SELECT RAISE(ABORT,'GL_AUDIT_IMMUTABLE'); END;

-- CLX-006 GL HARDENING + MONTH-END CONTROL
CREATE TABLE IF NOT EXISTS gl_fiscal_years(
 id INTEGER PRIMARY KEY, fiscal_year INTEGER NOT NULL UNIQUE, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED','LOCKED')), version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gl_periods(
 id INTEGER PRIMARY KEY, fiscal_year_id INTEGER NOT NULL REFERENCES gl_fiscal_years(id), period_no INTEGER NOT NULL,
 start_date TEXT NOT NULL, end_date TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED','LOCKED','FUTURE')),
 backdate_allowed_until TEXT, closed_at TEXT, locked_at TEXT, version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(fiscal_year_id,period_no)
);
CREATE TABLE IF NOT EXISTS gl_backdate_controls(
 id INTEGER PRIMARY KEY, period_id INTEGER NOT NULL UNIQUE REFERENCES gl_periods(id), earliest_posting_date TEXT NOT NULL,
 override_role TEXT NOT NULL DEFAULT 'ADMIN', active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gl_approval_rules(
 id INTEGER PRIMARY KEY, voucher_type TEXT NOT NULL, min_amount REAL NOT NULL DEFAULT 0, max_amount REAL,
 required_levels INTEGER NOT NULL DEFAULT 1, checker_role TEXT NOT NULL, maker_checker INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gl_approval_events(
 id INTEGER PRIMARY KEY, voucher_id INTEGER NOT NULL REFERENCES gl_vouchers(id) ON DELETE CASCADE, level INTEGER NOT NULL,
 actor_role TEXT NOT NULL, decision TEXT NOT NULL, ts TEXT NOT NULL, comment TEXT, UNIQUE(voucher_id,level)
);
CREATE TABLE IF NOT EXISTS gl_fx_rates(
 id INTEGER PRIMARY KEY, rate_date TEXT NOT NULL, currency TEXT NOT NULL, base_currency TEXT NOT NULL DEFAULT 'USD',
 rate REAL NOT NULL CHECK(rate>0), source TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE', UNIQUE(rate_date,currency,base_currency)
);
CREATE TABLE IF NOT EXISTS gl_fx_events(
 id INTEGER PRIMARY KEY, event_ref TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL CHECK(event_type IN ('REALIZED','UNREALIZED')),
 period_id INTEGER REFERENCES gl_periods(id), job_id INTEGER REFERENCES jobs(id), currency TEXT NOT NULL, foreign_amount REAL NOT NULL,
 old_rate REAL NOT NULL, new_rate REAL NOT NULL, gain_loss REAL NOT NULL, voucher_no TEXT, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gl_ar_open_items(
 id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), job_id INTEGER REFERENCES jobs(id), source_ref TEXT NOT NULL UNIQUE,
 document_date TEXT NOT NULL, due_date TEXT NOT NULL, currency TEXT NOT NULL, original_amount REAL NOT NULL, outstanding REAL NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gl_ap_open_items(
 id INTEGER PRIMARY KEY, supplier_name TEXT NOT NULL, job_id INTEGER REFERENCES jobs(id), source_ref TEXT NOT NULL UNIQUE,
 document_date TEXT NOT NULL, due_date TEXT NOT NULL, currency TEXT NOT NULL, original_amount REAL NOT NULL, outstanding REAL NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gl_credit_limits(
 id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL UNIQUE REFERENCES customers(id), currency TEXT NOT NULL, credit_limit REAL NOT NULL,
 exposure REAL NOT NULL DEFAULT 0, on_hold INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gl_bank_statement_items(
 id INTEGER PRIMARY KEY, statement_ref TEXT NOT NULL UNIQUE, bank_account_code TEXT NOT NULL REFERENCES gl_accounts(account_code),
 txn_date TEXT NOT NULL, description TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, matched INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gl_bank_matches(
 id INTEGER PRIMARY KEY, statement_item_id INTEGER NOT NULL REFERENCES gl_bank_statement_items(id), voucher_id INTEGER REFERENCES gl_vouchers(id),
 match_type TEXT NOT NULL CHECK(match_type IN ('AUTO','MANUAL')), matched_amount REAL NOT NULL, actor_role TEXT NOT NULL, ts TEXT NOT NULL,
 UNIQUE(statement_item_id,voucher_id)
);
CREATE TABLE IF NOT EXISTS gl_tax_postings(
 id INTEGER PRIMARY KEY, posting_ref TEXT NOT NULL UNIQUE, tax_type TEXT NOT NULL, source_ref TEXT NOT NULL, job_id INTEGER REFERENCES jobs(id),
 taxable_amount REAL NOT NULL, tax_amount REAL NOT NULL, debit_account TEXT NOT NULL REFERENCES gl_accounts(account_code),
 credit_account TEXT NOT NULL REFERENCES gl_accounts(account_code), voucher_no TEXT, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gl_period_close_checks(
 id INTEGER PRIMARY KEY, period_id INTEGER NOT NULL REFERENCES gl_periods(id), check_code TEXT NOT NULL, check_name TEXT NOT NULL,
 owner_role TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT, updated_at TEXT NOT NULL, UNIQUE(period_id,check_code)
);
CREATE TABLE IF NOT EXISTS gl_accrual_schedules(
 id INTEGER PRIMARY KEY, record_id INTEGER REFERENCES gl_records(id), job_id INTEGER REFERENCES jobs(id), period_id INTEGER NOT NULL REFERENCES gl_periods(id),
 amount REAL NOT NULL, accrued_account TEXT NOT NULL, offset_account TEXT NOT NULL, reverse_date TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gl_prepayment_schedules(
 id INTEGER PRIMARY KEY, record_id INTEGER REFERENCES gl_records(id), job_id INTEGER REFERENCES jobs(id), period_id INTEGER NOT NULL REFERENCES gl_periods(id),
 amount REAL NOT NULL, amortized REAL NOT NULL DEFAULT 0, asset_account TEXT NOT NULL, expense_account TEXT NOT NULL, status TEXT NOT NULL
);


-- CLX-007 Treasury / AR-AP settlement foundation
CREATE TABLE IF NOT EXISTS treasury_accounts(id INTEGER PRIMARY KEY,account_ref TEXT NOT NULL UNIQUE,account_type TEXT NOT NULL CHECK(account_type IN ('BANK','CASH')),account_name TEXT NOT NULL,bank_name TEXT,currency TEXT NOT NULL,opening_balance REAL NOT NULL DEFAULT 0,current_balance REAL NOT NULL DEFAULT 0,reserved_balance REAL NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'Active',version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS treasury_records(id INTEGER PRIMARY KEY,module TEXT NOT NULL,external_ref TEXT NOT NULL,job_id INTEGER REFERENCES jobs(id),party_type TEXT,party_name TEXT,currency TEXT NOT NULL DEFAULT 'USD',amount REAL NOT NULL DEFAULT 0,status TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,maker_id TEXT NOT NULL,checker_id TEXT,source_type TEXT,source_ref TEXT,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(module,external_ref));
CREATE TABLE IF NOT EXISTS treasury_allocations(id INTEGER PRIMARY KEY,treasury_record_id INTEGER NOT NULL REFERENCES treasury_records(id),source_type TEXT NOT NULL,source_ref TEXT NOT NULL,allocated_amount REAL NOT NULL,currency TEXT NOT NULL,fx_rate REAL NOT NULL DEFAULT 1,job_id INTEGER REFERENCES jobs(id),UNIQUE(treasury_record_id,source_type,source_ref));
CREATE TABLE IF NOT EXISTS treasury_payment_batches(id INTEGER PRIMARY KEY,batch_no TEXT NOT NULL UNIQUE,record_id INTEGER NOT NULL UNIQUE REFERENCES treasury_records(id),currency TEXT NOT NULL,total_amount REAL NOT NULL,status TEXT NOT NULL,maker_id TEXT NOT NULL,checker_id TEXT,released_by TEXT,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,approved_at TEXT,released_at TEXT);
CREATE TABLE IF NOT EXISTS treasury_batch_items(id INTEGER PRIMARY KEY,batch_id INTEGER NOT NULL REFERENCES treasury_payment_batches(id),source_type TEXT NOT NULL,source_ref TEXT NOT NULL,amount REAL NOT NULL,job_id INTEGER REFERENCES jobs(id));
CREATE TABLE IF NOT EXISTS treasury_cheques(id INTEGER PRIMARY KEY,cheque_no TEXT NOT NULL UNIQUE,record_id INTEGER REFERENCES treasury_records(id),bank_account_ref TEXT NOT NULL,party TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,cheque_date TEXT NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS treasury_bank_feed_items(id INTEGER PRIMARY KEY,bank_account_ref TEXT NOT NULL,statement_ref TEXT NOT NULL UNIQUE,txn_date TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,description TEXT NOT NULL,match_status TEXT NOT NULL DEFAULT 'UNMATCHED',matched_source_ref TEXT,exception_reason TEXT);
CREATE TABLE IF NOT EXISTS treasury_gl_links(id INTEGER PRIMARY KEY,treasury_record_id INTEGER NOT NULL REFERENCES treasury_records(id),voucher_id INTEGER REFERENCES gl_vouchers(id),link_type TEXT NOT NULL,source_ref TEXT NOT NULL,UNIQUE(treasury_record_id,link_type,source_ref));
CREATE TABLE IF NOT EXISTS treasury_reversals(id INTEGER PRIMARY KEY,original_record_id INTEGER NOT NULL REFERENCES treasury_records(id),reversal_record_id INTEGER NOT NULL REFERENCES treasury_records(id),original_voucher_id INTEGER REFERENCES gl_vouchers(id),reversal_voucher_id INTEGER REFERENCES gl_vouchers(id),reason TEXT NOT NULL,ts TEXT NOT NULL,UNIQUE(original_record_id));
CREATE TRIGGER IF NOT EXISTS treasury_audit_immutable_update BEFORE UPDATE ON audit_events WHEN OLD.module LIKE 'treasury:%' BEGIN SELECT RAISE(ABORT,'TREASURY_AUDIT_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS treasury_audit_immutable_delete BEFORE DELETE ON audit_events WHEN OLD.module LIKE 'treasury:%' BEGIN SELECT RAISE(ABORT,'TREASURY_AUDIT_IMMUTABLE'); END;

-- CLX-008 Finance Integration Sandbox + Security Controls
CREATE TABLE IF NOT EXISTS finance_providers(
 id INTEGER PRIMARY KEY, provider_key TEXT NOT NULL UNIQUE, provider_type TEXT NOT NULL,
 display_name TEXT NOT NULL, endpoint_mode TEXT NOT NULL DEFAULT 'MOCK', credential_ref TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'ENABLED', health_status TEXT NOT NULL DEFAULT 'HEALTHY',
 timeout_ms INTEGER NOT NULL DEFAULT 1500, max_retries INTEGER NOT NULL DEFAULT 3,
 circuit_state TEXT NOT NULL DEFAULT 'CLOSED', failure_count INTEGER NOT NULL DEFAULT 0,
 last_health_at TEXT, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS integration_records(
 id INTEGER PRIMARY KEY, module TEXT NOT NULL, external_ref TEXT NOT NULL, job_id INTEGER REFERENCES jobs(id),
 provider_id INTEGER REFERENCES finance_providers(id), office_scope TEXT NOT NULL, country_scope TEXT NOT NULL,
 status TEXT NOT NULL, payload_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(module,external_ref)
);
CREATE TABLE IF NOT EXISTS integration_events(
 id INTEGER PRIMARY KEY, event_ref TEXT NOT NULL UNIQUE, provider_id INTEGER NOT NULL REFERENCES finance_providers(id),
 event_type TEXT NOT NULL, idempotency_key TEXT, request_hash TEXT NOT NULL, request_redacted TEXT NOT NULL,
 response_redacted TEXT, status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
 next_retry_at TEXT, correlation_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(provider_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS integration_attempts(
 id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES integration_events(id), attempt_no INTEGER NOT NULL,
 result TEXT NOT NULL, latency_ms INTEGER NOT NULL DEFAULT 0, detail_redacted TEXT, ts TEXT NOT NULL,
 UNIQUE(event_id,attempt_no)
);
CREATE TABLE IF NOT EXISTS webhook_receipts(
 id INTEGER PRIMARY KEY, provider_id INTEGER NOT NULL REFERENCES finance_providers(id), external_event_id TEXT NOT NULL,
 payload_hash TEXT NOT NULL, signature_valid INTEGER NOT NULL, status TEXT NOT NULL, received_at TEXT NOT NULL,
 UNIQUE(provider_id,external_event_id)
);
CREATE TABLE IF NOT EXISTS bank_import_batches(
 id INTEGER PRIMARY KEY, import_ref TEXT NOT NULL UNIQUE, bank_account_ref TEXT NOT NULL, source_name TEXT NOT NULL,
 status TEXT NOT NULL, line_count INTEGER NOT NULL DEFAULT 0, matched_count INTEGER NOT NULL DEFAULT 0,
 unmatched_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bank_import_lines(
 id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES bank_import_batches(id) ON DELETE CASCADE,
 line_ref TEXT NOT NULL, txn_date TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL,
 description TEXT NOT NULL, status TEXT NOT NULL, matched_source_ref TEXT, failure_reason TEXT,
 UNIQUE(batch_id,line_ref)
);
CREATE TABLE IF NOT EXISTS sandbox_payment_requests(
 id INTEGER PRIMARY KEY, payment_ref TEXT NOT NULL UNIQUE, job_id INTEGER NOT NULL REFERENCES jobs(id),
 batch_id INTEGER NOT NULL REFERENCES treasury_payment_batches(id), voucher_id INTEGER REFERENCES gl_vouchers(id),
 beneficiary_name TEXT NOT NULL, beneficiary_account_masked TEXT NOT NULL, beneficiary_validated INTEGER NOT NULL DEFAULT 0,
 duplicate_fingerprint TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, payment_limit REAL NOT NULL,
 payment_date TEXT NOT NULL, approval_status TEXT NOT NULL, gl_control_status TEXT NOT NULL DEFAULT 'BALANCED', status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 simulated_provider_ref TEXT, released_by TEXT, released_at TEXT, UNIQUE(duplicate_fingerprint)
);
CREATE TABLE IF NOT EXISTS sandbox_payment_releases(
 id INTEGER PRIMARY KEY, payment_request_id INTEGER NOT NULL UNIQUE REFERENCES sandbox_payment_requests(id),
 provider_ref TEXT NOT NULL UNIQUE, release_mode TEXT NOT NULL DEFAULT 'SIMULATED', actor_id TEXT NOT NULL,
 ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS security_sessions(
 id INTEGER PRIMARY KEY, actor_id TEXT NOT NULL, role TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
 office_scope TEXT, country_scope TEXT, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 reauth_at TEXT, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS security_audit_events(
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, ts TEXT NOT NULL, actor_id TEXT, actor_role TEXT,
 action TEXT NOT NULL, resource TEXT NOT NULL, outcome TEXT NOT NULL, correlation_id TEXT NOT NULL,
 detail_redacted TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_limit_windows(
 actor_key TEXT PRIMARY KEY, window_start TEXT NOT NULL, request_count INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS security_audit_immutable_update BEFORE UPDATE ON security_audit_events
BEGIN SELECT RAISE(ABORT,'SECURITY_AUDIT_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS security_audit_immutable_delete BEFORE DELETE ON security_audit_events
BEGIN SELECT RAISE(ABORT,'SECURITY_AUDIT_IMMUTABLE'); END;


-- CLX-009 Bulk Build / Unified Operations Control
CREATE TABLE IF NOT EXISTS bulk_events(
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, ts TEXT NOT NULL, actor_role TEXT NOT NULL, actor_id TEXT,
 action TEXT NOT NULL, module_type TEXT, module TEXT, record_ref TEXT, job_ref TEXT, outcome TEXT NOT NULL, detail_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS bulk_events_immutable_update BEFORE UPDATE ON bulk_events
BEGIN SELECT RAISE(ABORT,'BULK_EVENT_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS bulk_events_immutable_delete BEFORE DELETE ON bulk_events
BEGIN SELECT RAISE(ABORT,'BULK_EVENT_IMMUTABLE'); END;

-- CLX-010 identity / administration / master-data governance
CREATE TABLE IF NOT EXISTS iam_organizations(id INTEGER PRIMARY KEY,org_code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE',version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS iam_countries(id INTEGER PRIMARY KEY,country_code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE');
CREATE TABLE IF NOT EXISTS iam_offices(id INTEGER PRIMARY KEY,office_code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,organization_id INTEGER NOT NULL REFERENCES iam_organizations(id),country_id INTEGER NOT NULL REFERENCES iam_countries(id),timezone TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE',version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS iam_users(id INTEGER PRIMARY KEY,user_ref TEXT NOT NULL UNIQUE,username TEXT NOT NULL UNIQUE,display_name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE',home_office_id INTEGER NOT NULL REFERENCES iam_offices(id),mfa_required INTEGER NOT NULL DEFAULT 1,failed_attempts INTEGER NOT NULL DEFAULT 0,locked_until TEXT,last_login_at TEXT,version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS iam_roles(id INTEGER PRIMARY KEY,role_code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,description TEXT,status TEXT NOT NULL DEFAULT 'ACTIVE',sensitive INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS iam_permissions(id INTEGER PRIMARY KEY,permission_code TEXT NOT NULL UNIQUE,module TEXT NOT NULL,action TEXT NOT NULL,sensitive INTEGER NOT NULL DEFAULT 0,UNIQUE(module,action));
CREATE TABLE IF NOT EXISTS iam_role_permissions(id INTEGER PRIMARY KEY,role_id INTEGER NOT NULL REFERENCES iam_roles(id),permission_id INTEGER NOT NULL REFERENCES iam_permissions(id),effect TEXT NOT NULL DEFAULT 'ALLOW' CHECK(effect IN ('ALLOW','DENY')),UNIQUE(role_id,permission_id));
CREATE TABLE IF NOT EXISTS iam_user_roles(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES iam_users(id),role_id INTEGER NOT NULL REFERENCES iam_roles(id),office_id INTEGER REFERENCES iam_offices(id),country_id INTEGER REFERENCES iam_countries(id),organization_id INTEGER REFERENCES iam_organizations(id),valid_from TEXT NOT NULL,valid_to TEXT,status TEXT NOT NULL DEFAULT 'ACTIVE',assigned_by TEXT NOT NULL,UNIQUE(user_id,role_id,office_id,country_id,organization_id));
CREATE TABLE IF NOT EXISTS iam_office_membership(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES iam_users(id),office_id INTEGER NOT NULL REFERENCES iam_offices(id),membership_type TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE',UNIQUE(user_id,office_id));
CREATE TABLE IF NOT EXISTS iam_scope_rules(id INTEGER PRIMARY KEY,rule_ref TEXT NOT NULL UNIQUE,role_code TEXT NOT NULL,scope_type TEXT NOT NULL CHECK(scope_type IN ('GLOBAL','ORGANIZATION','COUNTRY','OFFICE','OWN','CUSTOMER','AGENT')),scope_value TEXT,resource TEXT NOT NULL,action TEXT NOT NULL,effect TEXT NOT NULL CHECK(effect IN ('ALLOW','DENY')),priority INTEGER NOT NULL DEFAULT 100,status TEXT NOT NULL DEFAULT 'ACTIVE');
CREATE TABLE IF NOT EXISTS iam_sessions(id INTEGER PRIMARY KEY,session_token TEXT NOT NULL UNIQUE,user_id INTEGER NOT NULL REFERENCES iam_users(id),created_at TEXT NOT NULL,expires_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,mfa_verified INTEGER NOT NULL DEFAULT 0,reauth_at TEXT,revoked_at TEXT,status TEXT NOT NULL DEFAULT 'ACTIVE');
CREATE TABLE IF NOT EXISTS iam_login_audit(id INTEGER PRIMARY KEY,event_ref TEXT NOT NULL UNIQUE,ts TEXT NOT NULL,username TEXT NOT NULL,user_id INTEGER REFERENCES iam_users(id),event_type TEXT NOT NULL,outcome TEXT NOT NULL,detail_json TEXT NOT NULL,immutable_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS iam_security_policies(id INTEGER PRIMARY KEY,policy_key TEXT NOT NULL UNIQUE,policy_value TEXT NOT NULL,updated_at TEXT NOT NULL,updated_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS iam_delegations(id INTEGER PRIMARY KEY,delegation_ref TEXT NOT NULL UNIQUE,from_user_id INTEGER NOT NULL REFERENCES iam_users(id),to_user_id INTEGER NOT NULL REFERENCES iam_users(id),permission_code TEXT NOT NULL,valid_from TEXT NOT NULL,valid_to TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE',approved_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS iam_access_reviews(id INTEGER PRIMARY KEY,review_ref TEXT NOT NULL UNIQUE,user_id INTEGER NOT NULL REFERENCES iam_users(id),scope TEXT NOT NULL,status TEXT NOT NULL,reviewer TEXT NOT NULL,due_date TEXT NOT NULL,completed_at TEXT,result TEXT);
CREATE TABLE IF NOT EXISTS iam_service_accounts(id INTEGER PRIMARY KEY,account_ref TEXT NOT NULL UNIQUE,name TEXT NOT NULL,owner_user_id INTEGER NOT NULL REFERENCES iam_users(id),office_id INTEGER REFERENCES iam_offices(id),status TEXT NOT NULL DEFAULT 'ACTIVE',secret_state TEXT NOT NULL DEFAULT 'PLACEHOLDER_ONLY',last_rotated_at TEXT);
CREATE TABLE IF NOT EXISTS iam_api_clients(id INTEGER PRIMARY KEY,client_ref TEXT NOT NULL UNIQUE,name TEXT NOT NULL,owner_user_id INTEGER NOT NULL REFERENCES iam_users(id),allowed_scopes_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE',credential_state TEXT NOT NULL DEFAULT 'PLACEHOLDER_ONLY');
CREATE TABLE IF NOT EXISTS iam_audit_events(id INTEGER PRIMARY KEY,event_ref TEXT NOT NULL UNIQUE,ts TEXT NOT NULL,actor_user_ref TEXT NOT NULL,action TEXT NOT NULL,resource_type TEXT NOT NULL,resource_ref TEXT,scope_json TEXT NOT NULL,before_json TEXT,after_json TEXT,immutable_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS iam_sod_conflicts(id INTEGER PRIMARY KEY,conflict_code TEXT NOT NULL UNIQUE,role_a TEXT NOT NULL,role_b TEXT NOT NULL,reason TEXT NOT NULL,severity TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE');
CREATE TABLE IF NOT EXISTS iam_legal_entities(id INTEGER PRIMARY KEY,entity_code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,organization_id INTEGER NOT NULL REFERENCES iam_organizations(id),country_id INTEGER NOT NULL REFERENCES iam_countries(id),status TEXT NOT NULL DEFAULT 'ACTIVE');
CREATE TABLE IF NOT EXISTS iam_branches(id INTEGER PRIMARY KEY,branch_code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,office_id INTEGER NOT NULL REFERENCES iam_offices(id),status TEXT NOT NULL DEFAULT 'ACTIVE');
CREATE TABLE IF NOT EXISTS iam_departments(id INTEGER PRIMARY KEY,department_code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,branch_id INTEGER NOT NULL REFERENCES iam_branches(id),status TEXT NOT NULL DEFAULT 'ACTIVE');
CREATE TABLE IF NOT EXISTS iam_party_access(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES iam_users(id),party_type TEXT NOT NULL CHECK(party_type IN ('CUSTOMER','AGENT')),party_key TEXT NOT NULL,access_level TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE',UNIQUE(user_id,party_type,party_key));
CREATE TABLE IF NOT EXISTS iam_approval_limits(id INTEGER PRIMARY KEY,role_code TEXT NOT NULL,currency TEXT NOT NULL,amount_limit REAL NOT NULL,action TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE',UNIQUE(role_code,currency,action));
CREATE TABLE IF NOT EXISTS iam_temporary_access(id INTEGER PRIMARY KEY,temp_ref TEXT NOT NULL UNIQUE,user_id INTEGER NOT NULL REFERENCES iam_users(id),permission_code TEXT NOT NULL,valid_from TEXT NOT NULL,valid_to TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE',approved_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS iam_sso_readiness(id INTEGER PRIMARY KEY,provider_key TEXT NOT NULL UNIQUE,protocol TEXT NOT NULL,metadata_state TEXT NOT NULL,certificate_state TEXT NOT NULL,provisioning_state TEXT NOT NULL,status TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS iam_audit_immutable_update BEFORE UPDATE ON iam_audit_events BEGIN SELECT RAISE(ABORT,'IAM_AUDIT_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS iam_audit_immutable_delete BEFORE DELETE ON iam_audit_events BEGIN SELECT RAISE(ABORT,'IAM_AUDIT_IMMUTABLE'); END;