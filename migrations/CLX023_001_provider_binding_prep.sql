-- M3 CLX-023 provider credential-binding preparation.
-- Technical targets only. No secret values and no provider activation.

ALTER TABLE provider_live_configs ADD COLUMN IF NOT EXISTS vendor text;
ALTER TABLE provider_live_configs ADD COLUMN IF NOT EXISTS sandbox_base_url text;
ALTER TABLE provider_live_configs ADD COLUMN IF NOT EXISTS production_base_url text;
ALTER TABLE provider_live_configs ADD COLUMN IF NOT EXISTS auth_method text;
ALTER TABLE provider_live_configs ADD COLUMN IF NOT EXISTS auth_env_json text NOT NULL DEFAULT '[]';
ALTER TABLE provider_live_configs ADD COLUMN IF NOT EXISTS webhook_verification text;
ALTER TABLE provider_live_configs ADD COLUMN IF NOT EXISTS health_path text;
ALTER TABLE provider_live_configs ADD COLUMN IF NOT EXISTS scope_note text;

UPDATE provider_live_configs SET
  provider_key='truelayer-bank',
  display_name='TrueLayer Data API',
  vendor='TrueLayer',
  adapter_kind='TRUELAYER_DATA_V3',
  sandbox_base_url='https://api.truelayer-sandbox.com',
  production_base_url='https://api.truelayer.com',
  endpoint_env='M3_TRUELAYER_DATA_BASE_URL',
  credential_env='M3_TRUELAYER_CLIENT_SECRET',
  webhook_secret_env='M3_TRUELAYER_WEBHOOK_JWKS_URL',
  auth_method='OAuth2 client_credentials',
  auth_env_json='["M3_TRUELAYER_CLIENT_ID","M3_TRUELAYER_CLIENT_SECRET"]',
  webhook_verification='JWS/JWKS where applicable; sandbox JWKS allowlist only',
  health_path='/v3/data-connections',
  scope_note='Bank account, balance and transaction data. Requires explicit customer/bank consent where applicable.',
  timeout_ms=4000,max_retries=3,circuit_failure_threshold=3,
  enabled=0,real_money_capable=0,health_status='BINDING_PREP'
WHERE provider_key='bank-primary';

UPDATE provider_live_configs SET
  provider_key='truelayer-payments',
  display_name='TrueLayer Payments API v3',
  vendor='TrueLayer',
  adapter_kind='TRUELAYER_PAYMENTS_V3',
  sandbox_base_url='https://api.truelayer-sandbox.com',
  production_base_url='https://api.truelayer.com',
  endpoint_env='M3_TRUELAYER_PAYMENTS_BASE_URL',
  credential_env='M3_TRUELAYER_CLIENT_SECRET',
  webhook_secret_env='M3_TRUELAYER_WEBHOOK_JWKS_URL',
  auth_method='OAuth2 bearer + signed POST requests',
  auth_env_json='["M3_TRUELAYER_CLIENT_ID","M3_TRUELAYER_CLIENT_SECRET","M3_TRUELAYER_SIGNING_PRIVATE_KEY","M3_TRUELAYER_SIGNING_KEY_ID"]',
  webhook_verification='TL-Signature JWS verified against allowlisted TrueLayer JWKS',
  health_path='/v3/payments',
  scope_note='Pay-ins/payouts. Real-money capable and remains hard-blocked until separate approval.',
  timeout_ms=5000,max_retries=3,circuit_failure_threshold=3,
  enabled=0,real_money_capable=1,health_status='BINDING_PREP'
WHERE provider_key='payments-primary';

UPDATE provider_live_configs SET
  provider_key='openexchangerates-fx',
  display_name='Open Exchange Rates',
  vendor='Open Exchange Rates',
  adapter_kind='OPENEXCHANGERATES_V1',
  sandbox_base_url='https://openexchangerates.org/api',
  production_base_url='https://openexchangerates.org/api',
  endpoint_env='M3_OXR_BASE_URL',
  credential_env='M3_OXR_APP_ID',
  webhook_secret_env='M3_OXR_WEBHOOK_UNUSED',
  auth_method='Authorization: Token <app_id>',
  auth_env_json='["M3_OXR_APP_ID"]',
  webhook_verification='NONE; pull API',
  health_path='/latest.json',
  scope_note='Reference FX-rate feed only; no funds movement.',
  timeout_ms=2500,max_retries=3,circuit_failure_threshold=3,
  enabled=0,real_money_capable=0,health_status='BINDING_PREP'
WHERE provider_key='fx-primary';

UPDATE provider_live_configs SET
  provider_key='avalara-avatax',
  display_name='Avalara AvaTax',
  vendor='Avalara',
  adapter_kind='AVALARA_AVATAX_V2',
  sandbox_base_url='https://sandbox-rest.avatax.com',
  production_base_url='https://rest.avatax.com',
  endpoint_env='M3_AVALARA_BASE_URL',
  credential_env='M3_AVALARA_LICENSE_KEY',
  webhook_secret_env='M3_AVALARA_WEBHOOK_UNUSED',
  auth_method='Basic authentication / provider-supported credentials',
  auth_env_json='["M3_AVALARA_ACCOUNT_ID","M3_AVALARA_LICENSE_KEY","M3_AVALARA_CLIENT_HEADER"]',
  webhook_verification='NONE for core calculation binding; request/response correlation retained by M3',
  health_path='/api/v2/utilities/ping',
  scope_note='Transaction tax/VAT calculation. General withholding-tax calculation remains governed internally in M3 until a jurisdiction-specific WHT provider is selected.',
  timeout_ms=3500,max_retries=3,circuit_failure_threshold=3,
  enabled=0,real_money_capable=0,health_status='BINDING_PREP'
WHERE provider_key='tax-primary';
