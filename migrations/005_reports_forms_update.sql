-- Migration 005: Update reports and forms tables for Phase 4

-- Add filters column to reports (stores report generation parameters)
ALTER TABLE reports ADD COLUMN IF NOT EXISTS filters JSONB DEFAULT '{}';

-- Add theme and s3_url columns to forms
ALTER TABLE forms ADD COLUMN IF NOT EXISTS theme VARCHAR(50) DEFAULT 'default';
ALTER TABLE forms ADD COLUMN IF NOT EXISTS s3_url VARCHAR(500);

-- Add submission_data JSONB to form_submissions (stores field values)
ALTER TABLE form_submissions ADD COLUMN IF NOT EXISTS submission_data JSONB DEFAULT '{}';
