-- Rejected rows are never silently dropped: they land here with a reason_code.
-- The staging layer is rebuilt from scratch on every run (idempotent).
CREATE TABLE IF NOT EXISTS staging.quarantine (
    source_table   TEXT NOT NULL,
    record_key     TEXT NOT NULL,
    reason_code    TEXT NOT NULL,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
TRUNCATE staging.quarantine;
