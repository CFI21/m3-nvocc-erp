-- CLX-077 FINAL LIVE SESSION + DUMMY BANK VALIDATION
-- No parallel finance/bank model. Existing treasury, bank-import, GL, audit and provider tables remain authoritative.

INSERT INTO public.provider_live_configs(
 provider_key,provider_type,display_name,adapter_kind,endpoint_env,credential_env,webhook_secret_env,
 enabled,real_money_capable,timeout_ms,max_retries,circuit_failure_threshold,circuit_state,failure_count,
 health_status,version,created_at,updated_at,vendor,sandbox_base_url,production_base_url,auth_method,
 auth_env_json,webhook_verification,health_path,scope_note
) VALUES(
 'm3-dummy-bank','BANK','M3 DUMMY BANK — TEST ONLY — NO REAL MONEY','INTERNAL_SIMULATION_ONLY',
 'M3_INTERNAL_DUMMY_BANK','M3_INTERNAL_DUMMY_BANK','',1,0,100,3,3,'CLOSED',0,
 'SIMULATION_ONLY',1,now()::text,now()::text,'M3 INTERNAL',NULL,NULL,'NONE','{}','NONE',NULL,
 'CLX077_TEST / DUMMY_BANK only; no external network call; no real money'
)
ON CONFLICT(provider_key) DO UPDATE SET
 display_name=EXCLUDED.display_name,adapter_kind=EXCLUDED.adapter_kind,enabled=1,real_money_capable=0,
 circuit_state='CLOSED',failure_count=0,health_status='SIMULATION_ONLY',updated_at=now()::text,
 scope_note=EXCLUDED.scope_note;

INSERT INTO public.treasury_accounts(account_ref,account_type,account_name,bank_name,currency,opening_balance,current_balance,reserved_balance,status,version)
VALUES
('DUMMY-EUR-CLX077','BANK','M3 DUMMY BANK — TEST ONLY — NO REAL MONEY','M3 INTERNAL SIMULATION','EUR',100000,100000,0,'Active',1),
('DUMMY-USD-CLX077','BANK','M3 DUMMY BANK — TEST ONLY — NO REAL MONEY','M3 INTERNAL SIMULATION','USD',100000,100000,0,'Active',1)
ON CONFLICT(account_ref) DO UPDATE SET account_name=EXCLUDED.account_name,bank_name=EXCLUDED.bank_name,status='Active';

CREATE TABLE IF NOT EXISTS public.clx077_test_scenarios(
 scenario_no integer PRIMARY KEY,
 scenario_key text NOT NULL UNIQUE,
 title text NOT NULL,
 marker text NOT NULL DEFAULT 'CLX077_TEST',
 job_ref text,
 currency text NOT NULL,
 direction text NOT NULL,
 amount numeric NOT NULL,
 treasury_ref text,
 voucher_no text,
 bank_line_ref text,
 status text NOT NULL DEFAULT 'PENDING',
 detail_json text NOT NULL DEFAULT '{}',
 executed_at text
);

CREATE TABLE IF NOT EXISTS public.clx077_reconciliation_snapshots(
 run_ref text PRIMARY KEY,
 opening_eur numeric NOT NULL,
 opening_usd numeric NOT NULL,
 receipts_eur numeric NOT NULL,
 payments_eur numeric NOT NULL,
 reversals_eur numeric NOT NULL,
 receipts_usd numeric NOT NULL,
 payments_usd numeric NOT NULL,
 reversals_usd numeric NOT NULL,
 expected_eur numeric NOT NULL,
 actual_eur numeric NOT NULL,
 variance_eur numeric NOT NULL,
 expected_usd numeric NOT NULL,
 actual_usd numeric NOT NULL,
 variance_usd numeric NOT NULL,
 bank_import_unmatched integer NOT NULL,
 unbalanced_vouchers integer NOT NULL,
 duplicate_refs integer NOT NULL,
 status text NOT NULL,
 detail_json text NOT NULL,
 created_at text NOT NULL
);

ALTER TABLE public.clx077_test_scenarios ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clx077_reconciliation_snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.clx077_test_scenarios,public.clx077_reconciliation_snapshots FROM anon,authenticated;
