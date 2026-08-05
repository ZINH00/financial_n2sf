CREATE TABLE IF NOT EXISTS business_records (
  record_id BIGSERIAL PRIMARY KEY,
  case_id VARCHAR(64) NOT NULL,
  data_grade CHAR(1) NOT NULL CHECK (data_grade IN ('S','O')),
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_business_records_case ON business_records(case_id);
INSERT INTO business_records(case_id, data_grade, payload)
VALUES
  ('CASE-0001', 'S', '{"seed": true, "note": "synthetic financial workflow record"}'),
  ('CASE-0002', 'S', '{"seed": true, "note": "no real personal data"}')
ON CONFLICT DO NOTHING;
