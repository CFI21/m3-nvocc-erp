-- M3 Bulk Permission Governance — additive scope ownership only
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS office_code text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS branch_code text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS country_code text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS organization_code text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS created_by_user_ref text;

-- No user-job permission table is introduced.
-- Branch scope remains part of iam_scope_rules semantics.
