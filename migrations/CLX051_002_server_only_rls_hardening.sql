-- CLX-051 approved server-only deny-by-default RLS hardening.
-- No anon/authenticated policies are added. Server/Postgres runtime access remains authoritative.
ALTER TABLE public.provider_live_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_live_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_live_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_callback_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operations_work_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operations_work_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.management_daily_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.management_alert_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.management_alert_events ENABLE ROW LEVEL SECURITY;
