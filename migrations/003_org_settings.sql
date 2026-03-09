-- Migration 003: Organization settings for invoice customization
-- Stores company info, currency, tax settings per organization

CREATE TABLE IF NOT EXISTS org_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    setting_key VARCHAR(100) NOT NULL UNIQUE,
    setting_value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW(),
    updated_by UUID REFERENCES users(id)
);

-- Default settings
INSERT INTO org_settings (setting_key, setting_value) VALUES
    ('company_name', 'OmniDesk'),
    ('company_address', ''),
    ('company_phone', ''),
    ('company_email', ''),
    ('company_logo_s3_key', ''),
    ('currency_code', 'INR'),
    ('currency_symbol', '₹'),
    ('tax_label', 'GST'),
    ('payment_terms', 'Net 30'),
    ('invoice_footer', 'Thank you for your business'),
    ('locale', 'en-IN')
ON CONFLICT (setting_key) DO NOTHING;
