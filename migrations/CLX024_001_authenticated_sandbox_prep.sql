-- M3 CLX-024 authenticated sandbox onboarding preparation.
-- No secret values. No live-provider activation. No real-money movement.

UPDATE provider_live_configs
SET webhook_secret_env='',
    webhook_verification='NONE for Data API account reads',
    auth_env_json='["M3_TRUELAYER_CLIENT_ID","M3_TRUELAYER_CLIENT_SECRET","M3_TRUELAYER_DATA_CONNECTION_ID"]',
    health_status='SANDBOX_BINDING_PENDING',
    updated_at=CURRENT_TIMESTAMP::text
WHERE provider_key='truelayer-bank';

UPDATE provider_live_configs
SET webhook_secret_env='M3_TRUELAYER_WEBHOOK_JWKS_URL',
    auth_env_json='["M3_TRUELAYER_CLIENT_ID","M3_TRUELAYER_CLIENT_SECRET","M3_TRUELAYER_SIGNING_PRIVATE_KEY","M3_TRUELAYER_SIGNING_KEY_ID"]',
    health_status='SANDBOX_BINDING_PENDING',
    updated_at=CURRENT_TIMESTAMP::text
WHERE provider_key='truelayer-payments';

UPDATE provider_live_configs
SET webhook_secret_env='',
    webhook_verification='NONE; pull-only FX API',
    health_status='SANDBOX_BINDING_PENDING',
    updated_at=CURRENT_TIMESTAMP::text
WHERE provider_key='openexchangerates-fx';

UPDATE provider_live_configs
SET webhook_secret_env='',
    webhook_verification='NONE for sandbox ping/calculation request flow',
    health_status='SANDBOX_BINDING_PENDING',
    updated_at=CURRENT_TIMESTAMP::text
WHERE provider_key='avalara-avatax';