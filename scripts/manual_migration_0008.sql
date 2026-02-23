-- Check if qc_rule_versions table exists
SELECT table_name, column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'qc_rule_versions'
ORDER BY ordinal_position;

-- If table doesn't exist, create it manually
CREATE TABLE IF NOT EXISTS qc_rule_versions (
    id SERIAL PRIMARY KEY,
    rule_key VARCHAR(50) NOT NULL,
    version VARCHAR(20) NOT NULL DEFAULT '1.0.0',
    params_json JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(rule_key, version)
);

-- Create index
CREATE INDEX IF NOT EXISTS idx_qc_rule_versions_active ON qc_rule_versions(rule_key, is_active);

-- Insert seed data
INSERT INTO qc_rule_versions (rule_key, version, params_json, is_active)
VALUES 
    ('R001', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_03_expenditure", "null_as_zero": false}'::jsonb, TRUE),
    ('R002', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_03_expenditure", "classification_length": 3}'::jsonb, TRUE),
    ('R003', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_01_income_expenditure_total", "null_as_zero": false}'::jsonb, TRUE),
    ('R004', '1.0.0', '{"tolerance": 0.01, "economic_table": "FIN_06_basic_expenditure", "expenditure_table": "FIN_03_expenditure"}'::jsonb, TRUE),
    ('R005', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_07_three_public", "null_as_zero": false}'::jsonb, TRUE)
ON CONFLICT (rule_key, version) DO NOTHING;

-- Mark migration as applied
INSERT INTO schema_migrations (migration_id, applied_at)
VALUES ('2026-01-14_0008_rule_versioning', NOW())
ON CONFLICT (migration_id) DO NOTHING;

-- Verify
SELECT * FROM qc_rule_versions;
