-- CLX-051 production-readiness security hardening.
-- Preserve behavior while fixing Supabase function_search_path_mutable advisory.
ALTER FUNCTION public.prevent_management_snapshot_mutation()
SET search_path = public, pg_temp;
