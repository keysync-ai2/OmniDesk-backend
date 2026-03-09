-- Migration 006: Add submission_count to forms table
-- Tracks number of submissions per form for quick stats

ALTER TABLE forms ADD COLUMN IF NOT EXISTS submission_count INTEGER DEFAULT 0;
