-- 007: Invoice Templates
-- Adds customizable invoice template system with JSONB config for field toggles, themes, and branding.

CREATE TABLE IF NOT EXISTS invoice_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(200) NOT NULL DEFAULT 'Default',
    is_default BOOLEAN DEFAULT FALSE,
    config JSONB NOT NULL DEFAULT '{}',
    logo_s3_key VARCHAR(500),
    created_by UUID REFERENCES users(id),
    updated_at TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Only one default template allowed
CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_templates_default
    ON invoice_templates (is_default) WHERE is_default = TRUE;

-- Insert default template with all fields ON, professional_blue theme
INSERT INTO invoice_templates (name, is_default, config) VALUES (
    'Default', TRUE, '{
        "fields": {
            "company_logo": true,
            "company_name": true,
            "brand_name": false,
            "company_address": true,
            "company_phone": true,
            "company_email": true,
            "tagline": false,
            "invoice_number": true,
            "invoice_date": true,
            "due_date": true,
            "order_reference": true,
            "customer_name": true,
            "customer_email": true,
            "customer_phone": true,
            "customer_address": false,
            "item_number": true,
            "item_sku": true,
            "item_description": true,
            "item_quantity": true,
            "item_unit_price": true,
            "item_line_total": true,
            "subtotal": true,
            "tax_line": true,
            "grand_total": true,
            "payment_terms": true,
            "notes": true,
            "footer_text": true,
            "powered_by_omnidesk": true
        },
        "custom_text": {
            "brand_name": "",
            "tagline": "",
            "invoice_prefix": "INV",
            "footer_text": ""
        },
        "theme": "professional_blue"
    }'
);
