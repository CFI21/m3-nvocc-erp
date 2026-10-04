-- M3 FINAL BULK COMPLETION — governed planning metadata on existing gl_budget_lines only.
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS budget_ref text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS scenario text NOT NULL DEFAULT 'BUDGET';
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS job_ref text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS branch_code text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS office_code text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS country_code text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS organization_code text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS cost_center text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS profit_center text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'DRAFT';
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS requested_by text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS requested_at text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS approved_by text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS approved_at text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS revision_no integer NOT NULL DEFAULT 1;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS parent_budget_ref text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS revision_reason text;
ALTER TABLE public.gl_budget_lines ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;

ALTER TABLE public.gl_budget_lines DROP CONSTRAINT IF EXISTS gl_budget_lines_fiscal_year_period_account_code_key;
CREATE UNIQUE INDEX IF NOT EXISTS ux_gl_budget_ref ON public.gl_budget_lines(budget_ref) WHERE budget_ref IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_gl_budget_scope
ON public.gl_budget_lines(
 fiscal_year,period,account_code,scenario,
 COALESCE(job_ref,''),COALESCE(branch_code,''),COALESCE(office_code,''),
 COALESCE(country_code,''),COALESCE(organization_code,''),
 COALESCE(cost_center,''),COALESCE(profit_center,''),revision_no
);
