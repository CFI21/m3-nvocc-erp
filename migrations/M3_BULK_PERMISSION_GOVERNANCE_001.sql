-- M3 Bulk Permission Governance — additive scope ownership only
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS office_code text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS branch_code text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS country_code text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS organization_code text;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS created_by_user_ref text;

-- No user-job permission table is introduced.
-- Branch scope remains part of iam_scope_rules semantics.

DO $$
DECLARE cname text;
BEGIN
  SELECT conname INTO cname
  FROM pg_constraint
  WHERE conrelid='public.iam_scope_rules'::regclass
    AND contype='c'
    AND pg_get_constraintdef(oid) LIKE '%scope_type%';
  IF cname IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.iam_scope_rules DROP CONSTRAINT %I',cname);
  END IF;
  ALTER TABLE public.iam_scope_rules
    ADD CONSTRAINT iam_scope_rules_scope_type_check
    CHECK(scope_type IN ('GLOBAL','ORGANIZATION','COUNTRY','OFFICE','BRANCH','OWN','CUSTOMER','AGENT'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
