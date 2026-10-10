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
