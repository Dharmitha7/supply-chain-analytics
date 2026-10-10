-- Reference / dimension sources: category translation, customers, sellers, products, geolocation.

-- category translation (+ 'unknown' sentinel so missing categories join cleanly)
DROP TABLE IF EXISTS staging.category_translation CASCADE;
CREATE TABLE staging.category_translation AS
SELECT DISTINCT ON (btrim(product_category_name))
       btrim(product_category_name)         AS category_pt,
       btrim(product_category_name_english) AS category_en
FROM raw.category_translation
WHERE btrim(product_category_name) <> ''
ORDER BY btrim(product_category_name);
INSERT INTO staging.category_translation VALUES ('unknown', 'unknown') ON CONFLICT DO NOTHING;
ALTER TABLE staging.category_translation ADD PRIMARY KEY (category_pt);

-- customers: dedupe on customer_id
DROP TABLE IF EXISTS staging.customers CASCADE;
CREATE TABLE staging.customers AS
WITH c AS (
  SELECT btrim(customer_id) AS customer_id, btrim(customer_unique_id) AS customer_unique_id,
         lpad(btrim(customer_zip_code_prefix), 5, '0') AS zip_prefix,
         lower(btrim(customer_city)) AS city, upper(btrim(customer_state)) AS state,
         CASE WHEN btrim(customer_id) = '' THEN 'MISSING_KEY'
              WHEN row_number() OVER (PARTITION BY btrim(customer_id) ORDER BY customer_unique_id) > 1 THEN 'DUPLICATE_KEY'
         END AS reason
  FROM raw.customers)
SELECT customer_id, customer_unique_id, zip_prefix, city, state FROM c WHERE reason IS NULL;
ALTER TABLE staging.customers ADD PRIMARY KEY (customer_id);

INSERT INTO staging.quarantine(source_table, record_key, reason_code)
SELECT 'customers', customer_id, reason FROM (
  SELECT btrim(customer_id) AS customer_id,
         CASE WHEN btrim(customer_id) = '' THEN 'MISSING_KEY'
              WHEN row_number() OVER (PARTITION BY btrim(customer_id) ORDER BY customer_unique_id) > 1 THEN 'DUPLICATE_KEY' END AS reason
  FROM raw.customers) x WHERE reason IS NOT NULL;

-- sellers (= suppliers)
DROP TABLE IF EXISTS staging.sellers CASCADE;
CREATE TABLE staging.sellers AS
SELECT DISTINCT ON (btrim(seller_id))
       btrim(seller_id) AS seller_id, lpad(btrim(seller_zip_code_prefix), 5, '0') AS zip_prefix,
       lower(btrim(seller_city)) AS city, upper(btrim(seller_state)) AS state
FROM raw.sellers WHERE btrim(seller_id) <> ''
ORDER BY btrim(seller_id);
ALTER TABLE staging.sellers ADD PRIMARY KEY (seller_id);

INSERT INTO staging.quarantine(source_table, record_key, reason_code)
SELECT 'sellers', seller_id, CASE WHEN seller_id = '' THEN 'MISSING_KEY' ELSE 'DUPLICATE_KEY' END FROM (
  SELECT btrim(seller_id) AS seller_id, row_number() OVER (PARTITION BY btrim(seller_id)) AS rn FROM raw.sellers) x
WHERE rn > 1 OR seller_id = '';

-- products: null category -> 'unknown'; zero/blank measures -> NULL (value invalid, row kept)
DROP TABLE IF EXISTS staging.products CASCADE;
CREATE TABLE staging.products AS
SELECT DISTINCT ON (btrim(p.product_id))
       btrim(p.product_id) AS product_id,
       COALESCE(NULLIF(btrim(p.product_category_name), ''), 'unknown') AS category_pt,
       NULLIF(NULLIF(p.product_weight_g, '')::numeric, 0) AS weight_g,
       NULLIF(NULLIF(p.product_length_cm, '')::numeric, 0) AS length_cm,
       NULLIF(NULLIF(p.product_height_cm, '')::numeric, 0) AS height_cm,
       NULLIF(NULLIF(p.product_width_cm, '')::numeric, 0)  AS width_cm,
       (NULLIF(btrim(p.product_category_name), '') IS NULL) AS category_was_missing
FROM raw.products p WHERE btrim(p.product_id) <> ''
ORDER BY btrim(p.product_id);
-- categories absent from the translation file get an identity mapping so FKs hold
INSERT INTO staging.category_translation
SELECT DISTINCT category_pt, category_pt FROM staging.products ON CONFLICT DO NOTHING;
ALTER TABLE staging.products ADD PRIMARY KEY (product_id);

INSERT INTO staging.quarantine(source_table, record_key, reason_code)
SELECT 'products', product_id, CASE WHEN product_id = '' THEN 'MISSING_KEY' ELSE 'DUPLICATE_KEY' END FROM (
  SELECT btrim(product_id) AS product_id, row_number() OVER (PARTITION BY btrim(product_id)) AS rn FROM raw.products) x
WHERE rn > 1 OR product_id = '';

-- geolocation: ~1M rows, many per zip prefix. Collapsed to one row per prefix (avg lat/lng).
-- This is an aggregation, not a rejection; the collapse is logged in dq.run_log.
DROP TABLE IF EXISTS staging.geolocation CASCADE;
CREATE TABLE staging.geolocation AS
SELECT lpad(btrim(geolocation_zip_code_prefix), 5, '0') AS zip_prefix,
       avg(geolocation_lat::double precision) AS lat,
       avg(geolocation_lng::double precision) AS lng,
       mode() WITHIN GROUP (ORDER BY upper(btrim(geolocation_state))) AS state
FROM raw.geolocation
WHERE NULLIF(geolocation_lat, '') IS NOT NULL AND NULLIF(geolocation_lng, '') IS NOT NULL
GROUP BY 1;
ALTER TABLE staging.geolocation ADD PRIMARY KEY (zip_prefix);

INSERT INTO dq.run_log(step, table_name, rows_in, rows_out) VALUES
 ('staging', 'staging.category_translation', (SELECT count(*) FROM raw.category_translation), (SELECT count(*) FROM staging.category_translation)),
 ('staging', 'staging.customers',   (SELECT count(*) FROM raw.customers),   (SELECT count(*) FROM staging.customers)),
 ('staging', 'staging.sellers',     (SELECT count(*) FROM raw.sellers),     (SELECT count(*) FROM staging.sellers)),
 ('staging', 'staging.products',    (SELECT count(*) FROM raw.products),    (SELECT count(*) FROM staging.products)),
 ('staging', 'staging.geolocation', (SELECT count(*) FROM raw.geolocation), (SELECT count(*) FROM staging.geolocation));
