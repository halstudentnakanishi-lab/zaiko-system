-- Existing MariaDB databases only. Select your database before running.
-- ADD COLUMN IF NOT EXISTS allows safe repeated application on MariaDB 10.4+.
-- Historical unknown quantities/reasons stay NULL; no values are invented.
ALTER TABLE inventory_transactions
  ADD COLUMN IF NOT EXISTS reason_code VARCHAR(50) NULL,
  ADD COLUMN IF NOT EXISTS before_quantity INT NULL,
  ADD COLUMN IF NOT EXISTS after_quantity INT NULL;
