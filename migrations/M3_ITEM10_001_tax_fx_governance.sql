-- M3 Item 10 Tax / VAT / WHT + FX + Multi-Currency Governance
-- Additive metadata only; reuses existing tax master, GL, FX and treasury models.
ALTER TABLE public.gl_periods ADD COLUMN IF NOT EXISTS tax_filed_at text;
ALTER TABLE public.gl_periods ADD COLUMN IF NOT EXISTS tax_filed_by text;

ALTER TABLE public.gl_tax_postings ADD COLUMN IF NOT EXISTS tax_code text;
ALTER TABLE public.gl_tax_postings ADD COLUMN IF NOT EXISTS jurisdiction text;
ALTER TABLE public.gl_tax_postings ADD COLUMN IF NOT EXISTS treatment text;
ALTER TABLE public.gl_tax_postings ADD COLUMN IF NOT EXISTS rate numeric;
ALTER TABLE public.gl_tax_postings ADD COLUMN IF NOT EXISTS rate_source text;
ALTER TABLE public.gl_tax_postings ADD COLUMN IF NOT EXISTS source_currency text;
ALTER TABLE public.gl_tax_postings ADD COLUMN IF NOT EXISTS base_currency text DEFAULT 'USD';
ALTER TABLE public.gl_tax_postings ADD COLUMN IF NOT EXISTS exchange_rate numeric DEFAULT 1;
