-- Migration 002: Add warehouse_id to stock_movements
-- Required for multi-warehouse stock tracking (Phase 2)

ALTER TABLE stock_movements ADD COLUMN warehouse_id UUID REFERENCES warehouses(id);

-- Index for faster lookups by product + warehouse
CREATE INDEX IF NOT EXISTS idx_stock_movements_product_warehouse
    ON stock_movements(product_id, warehouse_id);
