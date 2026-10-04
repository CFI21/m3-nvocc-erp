-- M3 Item 13 Year-End Close + Audit + Statutory Governance
-- Additive metadata on the existing authoritative fiscal-year model only.
ALTER TABLE public.gl_fiscal_years ADD COLUMN IF NOT EXISTS closed_at text;
ALTER TABLE public.gl_fiscal_years ADD COLUMN IF NOT EXISTS closed_by text;
ALTER TABLE public.gl_fiscal_years ADD COLUMN IF NOT EXISTS locked_at text;
ALTER TABLE public.gl_fiscal_years ADD COLUMN IF NOT EXISTS locked_by text;
ALTER TABLE public.gl_fiscal_years ADD COLUMN IF NOT EXISTS reopened_at text;
ALTER TABLE public.gl_fiscal_years ADD COLUMN IF NOT EXISTS reopened_by text;
ALTER TABLE public.gl_fiscal_years ADD COLUMN IF NOT EXISTS reopen_reason text;
ALTER TABLE public.gl_fiscal_years ADD COLUMN IF NOT EXISTS retained_earnings_account text DEFAULT '3000';
ALTER TABLE public.gl_fiscal_years ADD COLUMN IF NOT EXISTS carry_forward_ref text;
