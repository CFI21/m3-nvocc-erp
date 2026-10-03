-- Supabase migration 20261003075052
-- Harden immutable screen-integration trigger function search_path.
ALTER FUNCTION public.prevent_screen_integration_event_mutation()
SET search_path = pg_catalog, public;
