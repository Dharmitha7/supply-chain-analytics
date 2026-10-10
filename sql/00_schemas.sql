-- Layered schemas: raw -> staging -> core -> marts ; sim = simulated inputs ; dq = quality
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS sim;   -- SIMULATED data only (inventory, purchase orders)
CREATE SCHEMA IF NOT EXISTS dq;

CREATE TABLE IF NOT EXISTS dq.run_log (
    run_id      BIGSERIAL PRIMARY KEY,
    step        TEXT NOT NULL,
    table_name  TEXT,
    rows_in     BIGINT,
    rows_out    BIGINT,
    logged_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Data-quality framework: check definitions (loaded from sql/90_dq/checks.sql) and append-only results.
CREATE TABLE IF NOT EXISTS dq.check (
    check_id    TEXT PRIMARY KEY,
    layer       TEXT NOT NULL CHECK (layer IN ('raw', 'staging', 'core', 'marts')),
    table_name  TEXT NOT NULL,
    check_type  TEXT NOT NULL,
    description TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('critical', 'warn')),
    sql         TEXT NOT NULL,                 -- returns ONE number: a violation count / metric
    threshold   NUMERIC NOT NULL DEFAULT 0     -- check passes when value <= threshold
);
CREATE TABLE IF NOT EXISTS dq.result (
    result_id  BIGSERIAL PRIMARY KEY,
    run_id     TEXT NOT NULL,
    check_id   TEXT NOT NULL,
    layer      TEXT NOT NULL,
    table_name TEXT NOT NULL,
    check_type TEXT NOT NULL,
    severity   TEXT NOT NULL,
    value      NUMERIC,
    threshold  NUMERIC,
    passed     BOOLEAN NOT NULL,
    error      TEXT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
