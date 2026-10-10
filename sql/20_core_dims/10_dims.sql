-- Core dimensions. Rebuilt from staging on every run. Surrogate keys are deterministic (ordered by natural key).
-- Dropping facts first (CASCADE also drops dependent marts views, which are rebuilt later in the pipeline).
DROP TABLE IF EXISTS core.fact_inventory_snapshot, core.fact_purchase_order,
                     core.fact_shipment, core.fact_order_line CASCADE;
DROP TABLE IF EXISTS core.dim_date, core.dim_product, core.dim_supplier,
                     core.dim_customer, core.dim_warehouse, core.map_state_warehouse CASCADE;

-- ---------- dim_date ----------
CREATE TABLE core.dim_date (
    date_key    INT PRIMARY KEY,           -- yyyymmdd
    date        DATE NOT NULL UNIQUE,
    year        INT NOT NULL,
    quarter     INT NOT NULL,
    month       INT NOT NULL,
    month_name  TEXT NOT NULL,
    year_month  TEXT NOT NULL,             -- 'YYYY-MM' sortable label
    week_start  DATE NOT NULL,             -- Monday
    iso_week    INT NOT NULL,
    day_of_week INT NOT NULL,              -- 1=Mon .. 7=Sun
    is_weekend  BOOLEAN NOT NULL
);
INSERT INTO core.dim_date
SELECT to_char(d, 'YYYYMMDD')::int, d::date, extract(year FROM d)::int, extract(quarter FROM d)::int,
       extract(month FROM d)::int, to_char(d, 'FMMonth'), to_char(d, 'YYYY-MM'),
       date_trunc('week', d)::date, extract(week FROM d)::int, extract(isodow FROM d)::int,
       extract(isodow FROM d) >= 6
FROM generate_series(
       (SELECT date_trunc('year', min(least(purchase_ts, estimated_ts)))::date FROM staging.orders),
       (SELECT (date_trunc('year', max(greatest(purchase_ts, estimated_ts, delivered_ts))) + interval '1 year - 1 day')::date FROM staging.orders),
       interval '1 day') AS g(d);

-- ---------- dim_warehouse (SIMULATED: Olist has no warehouse concept) ----------
CREATE TABLE core.dim_warehouse (
    warehouse_key  INT PRIMARY KEY,
    warehouse_code TEXT NOT NULL UNIQUE,
    warehouse_name TEXT NOT NULL,
    city           TEXT NOT NULL,
    region         TEXT NOT NULL,
    is_simulated   BOOLEAN NOT NULL DEFAULT TRUE
);
INSERT INTO core.dim_warehouse VALUES
  (1, 'DC-SE', 'Southeast DC',     'sao paulo', 'Southeast',       TRUE),
  (2, 'DC-S',  'South DC',         'curitiba',  'South',           TRUE),
  (3, 'DC-NE', 'Northeast DC',     'recife',    'Northeast',       TRUE),
  (4, 'DC-CN', 'Center-North DC',  'brasilia',  'Center-North',    TRUE);

-- customer state -> nearest regional DC (SIMULATED assignment)
CREATE TABLE core.map_state_warehouse (
    state         TEXT PRIMARY KEY,
    warehouse_key INT NOT NULL REFERENCES core.dim_warehouse,
    is_simulated  BOOLEAN NOT NULL DEFAULT TRUE
);
INSERT INTO core.map_state_warehouse(state, warehouse_key)
SELECT s, w FROM (VALUES
  ('SP',1),('RJ',1),('MG',1),('ES',1),
  ('PR',2),('SC',2),('RS',2),
  ('BA',3),('PE',3),('CE',3),('AL',3),('SE',3),('RN',3),('PB',3),('PI',3),('MA',3),
  ('DF',4),('GO',4),('MT',4),('MS',4),('TO',4),('AM',4),('PA',4),('AC',4),('AP',4),('RO',4),('RR',4)
) v(s, w);

-- ---------- dim_customer ----------
CREATE TABLE core.dim_customer (
    customer_key       INT PRIMARY KEY,
    customer_id        TEXT NOT NULL UNIQUE,
    customer_unique_id TEXT NOT NULL,
    zip_prefix         TEXT,
    city               TEXT,
    state              TEXT,
    lat                DOUBLE PRECISION,
    lng                DOUBLE PRECISION,
    warehouse_key      INT NOT NULL REFERENCES core.dim_warehouse
);
INSERT INTO core.dim_customer
SELECT row_number() OVER (ORDER BY c.customer_id), c.customer_id, c.customer_unique_id, c.zip_prefix, c.city, c.state,
       g.lat, g.lng, COALESCE(m.warehouse_key, 1)   -- unmapped / unknown state defaults to the Southeast DC
FROM staging.customers c
LEFT JOIN staging.geolocation g ON g.zip_prefix = c.zip_prefix
LEFT JOIN core.map_state_warehouse m ON m.state = c.state;

-- ---------- dim_supplier (= Olist seller) ----------
CREATE TABLE core.dim_supplier (
    supplier_key INT PRIMARY KEY,
    seller_id    TEXT NOT NULL UNIQUE,
    zip_prefix   TEXT,
    city         TEXT,
    state        TEXT,
    lat          DOUBLE PRECISION,
    lng          DOUBLE PRECISION
);
INSERT INTO core.dim_supplier
SELECT row_number() OVER (ORDER BY s.seller_id), s.seller_id, s.zip_prefix, s.city, s.state, g.lat, g.lng
FROM staging.sellers s LEFT JOIN staging.geolocation g ON g.zip_prefix = s.zip_prefix;

-- ---------- dim_product ----------
CREATE TABLE core.dim_product (
    product_key  INT PRIMARY KEY,
    product_id   TEXT NOT NULL UNIQUE,
    category_pt  TEXT NOT NULL,
    category_en  TEXT NOT NULL,
    weight_g     NUMERIC,
    length_cm    NUMERIC,
    height_cm    NUMERIC,
    width_cm     NUMERIC,
    volume_cm3   NUMERIC
);
INSERT INTO core.dim_product
SELECT row_number() OVER (ORDER BY p.product_id), p.product_id, p.category_pt, t.category_en,
       p.weight_g, p.length_cm, p.height_cm, p.width_cm, p.length_cm * p.height_cm * p.width_cm
FROM staging.products p JOIN staging.category_translation t ON t.category_pt = p.category_pt;

INSERT INTO dq.run_log(step, table_name, rows_in, rows_out) VALUES
 ('core', 'core.dim_customer', (SELECT count(*) FROM staging.customers), (SELECT count(*) FROM core.dim_customer)),
 ('core', 'core.dim_supplier', (SELECT count(*) FROM staging.sellers),   (SELECT count(*) FROM core.dim_supplier)),
 ('core', 'core.dim_product',  (SELECT count(*) FROM staging.products),  (SELECT count(*) FROM core.dim_product));
