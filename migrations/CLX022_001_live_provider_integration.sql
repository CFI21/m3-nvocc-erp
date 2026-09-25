-- M3 CLX-022 Live Provider Integration Core (safe/readiness only)
-- No live credentials, provider activation, or real-money movement is performed by this migration.

CREATE TABLE IF NOT EXISTS provider_live_configs (
  provider_key text PRIMARY KEY,
  provider_type text NOT NULL CHECK (provider_type IN ('BANK','PAYMENT','FX','TAX')),
  display_name text NOT NULL,
  adapter_kind text NOT NULL DEFAULT 'GENERIC_HTTP',
  endpoint_env text NOT NULL,
  credential_env text NOT NULL,
  webhook_secret_env text NOT NULL,
  enabled integer NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
  real_money_capable integer NOT NULL DEFAULT 0 CHECK (real_money_capable IN (0,1)),
  timeout_ms integer NOT NULL DEFAULT 3000 CHECK (timeout_ms BETWEEN 250 AND 30000),
  max_retries integer NOT NULL DEFAULT 3 CHECK (max_retries BETWEEN 0 AND 10),
  circuit_failure_threshold integer NOT NULL DEFAULT 3 CHECK (circuit_failure_threshold BETWEEN 1 AND 20),
  circuit_state text NOT NULL DEFAULT 'CLOSED' CHECK (circuit_state IN ('CLOSED','OPEN','HALF_OPEN')),
  failure_count integer NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
  health_status text NOT NULL DEFAULT 'NOT_CONFIGURED',
  last_health_at text,
  version integer NOT NULL DEFAULT 1,
  created_at text NOT NULL,
  updated_at text NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_live_events (
  id bigserial PRIMARY KEY,
  event_ref text NOT NULL UNIQUE,
  provider_key text NOT NULL REFERENCES provider_live_configs(provider_key),
  event_type text NOT NULL,
  idempotency_key text NOT NULL,
  request_hash text NOT NULL,
  request_redacted text NOT NULL,
  response_redacted text,
  status text NOT NULL CHECK (status IN ('READY','DELIVERED','RETRY','DEAD_LETTER','BLOCKED')),
  attempt_count integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 3,
  next_retry_at text,
  correlation_id text NOT NULL,
  created_at text NOT NULL,
  updated_at text NOT NULL,
  UNIQUE(provider_key,idempotency_key)
);

CREATE TABLE IF NOT EXISTS provider_live_attempts (
  id bigserial PRIMARY KEY,
  event_id bigint NOT NULL REFERENCES provider_live_events(id),
  attempt_no integer NOT NULL,
  result text NOT NULL,
  latency_ms integer NOT NULL DEFAULT 0,
  detail_redacted text,
  ts text NOT NULL,
  UNIQUE(event_id,attempt_no)
);

CREATE TABLE IF NOT EXISTS provider_callback_receipts (
  id bigserial PRIMARY KEY,
  provider_key text NOT NULL REFERENCES provider_live_configs(provider_key),
  external_event_id text NOT NULL,
  payload_hash text NOT NULL,
  signature_valid integer NOT NULL CHECK (signature_valid IN (0,1)),
  status text NOT NULL,
  received_at text NOT NULL,
  UNIQUE(provider_key,external_event_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_live_events_status ON provider_live_events(status,next_retry_at);
CREATE INDEX IF NOT EXISTS idx_provider_callback_receipts_provider ON provider_callback_receipts(provider_key,received_at);

INSERT INTO provider_live_configs(
 provider_key,provider_type,display_name,adapter_kind,endpoint_env,credential_env,webhook_secret_env,
 enabled,real_money_capable,timeout_ms,max_retries,circuit_failure_threshold,circuit_state,failure_count,
 health_status,created_at,updated_at
) VALUES
 ('bank-primary','BANK','Primary Live Bank','GENERIC_HTTP','M3_BANK_API_URL','M3_BANK_API_TOKEN','M3_BANK_WEBHOOK_SECRET',0,1,4000,3,3,'CLOSED',0,'NOT_CONFIGURED',CURRENT_TIMESTAMP::text,CURRENT_TIMESTAMP::text),
 ('payments-primary','PAYMENT','Primary Live Payments','GENERIC_HTTP','M3_PAYMENT_API_URL','M3_PAYMENT_API_TOKEN','M3_PAYMENT_WEBHOOK_SECRET',0,1,4000,3,3,'CLOSED',0,'NOT_CONFIGURED',CURRENT_TIMESTAMP::text,CURRENT_TIMESTAMP::text),
 ('fx-primary','FX','Primary Live FX','GENERIC_HTTP','M3_FX_API_URL','M3_FX_API_TOKEN','M3_FX_WEBHOOK_SECRET',0,0,2500,3,3,'CLOSED',0,'NOT_CONFIGURED',CURRENT_TIMESTAMP::text,CURRENT_TIMESTAMP::text),
 ('tax-primary','TAX','Primary Live Tax/WHT','GENERIC_HTTP','M3_TAX_API_URL','M3_TAX_API_TOKEN','M3_TAX_WEBHOOK_SECRET',0,0,3000,3,3,'CLOSED',0,'NOT_CONFIGURED',CURRENT_TIMESTAMP::text,CURRENT_TIMESTAMP::text)
ON CONFLICT (provider_key) DO NOTHING;
