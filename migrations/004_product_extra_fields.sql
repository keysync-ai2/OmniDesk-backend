-- Migration 004: Add extra_fields JSONB column to products
-- Stores additional product attributes (origin, weight, supplier, license, expiry, etc.)

ALTER TABLE products ADD COLUMN IF NOT EXISTS extra_fields JSONB DEFAULT '{}';
