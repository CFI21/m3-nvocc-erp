-- M3 Item 11 Period Close + Month-End + Financial Statement Governance
-- Additive governance metadata only; no new period or reporting model.
ALTER TABLE public.gl_periods ADD COLUMN IF NOT EXISTS closed_by text;
ALTER TABLE public.gl_periods ADD COLUMN IF NOT EXISTS locked_by text;
ALTER TABLE public.gl_periods ADD COLUMN IF NOT EXISTS reopened_at text;
ALTER TABLE public.gl_periods ADD COLUMN IF NOT EXISTS reopened_by text;
ALTER TABLE public.gl_periods ADD COLUMN IF NOT EXISTS reopen_reason text;
