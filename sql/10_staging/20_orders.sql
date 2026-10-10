-- Orders: typed timestamps; hard-invalid rows -> quarantine; soft anomalies -> flags (row kept).
-- Classification is computed once in a temp table so staging and quarantine can never disagree.
DROP TABLE IF EXISTS pg_temp.order_class;
CREATE TEMP TABLE order_class AS
WITH t AS (
  SELECT btrim(order_id) AS order_id, btrim(customer_id) AS customer_id, lower(btrim(order_status)) AS status,
         NULLIF(order_purchase_timestamp, '')::timestamp AS purchase_ts,
         NULLIF(order_approved_at, '')::timestamp AS approved_ts,
         NULLIF(order_delivered_carrier_date, '')::timestamp AS carrier_ts,
         NULLIF(order_delivered_customer_date, '')::timestamp AS delivered_ts,
         NULLIF(order_estimated_delivery_date, '')::timestamp AS estimated_ts
  FROM raw.orders)
SELECT t.*,
  CASE WHEN order_id = '' THEN 'MISSING_KEY'
       WHEN row_number() OVER (PARTITION BY order_id ORDER BY purchase_ts) > 1 THEN 'DUPLICATE_KEY'
       WHEN purchase_ts IS NULL THEN 'MISSING_PURCHASE_DATE'
       WHEN NOT EXISTS (SELECT 1 FROM staging.customers c WHERE c.customer_id = t.customer_id) THEN 'ORPHAN_CUSTOMER'
       WHEN delivered_ts < purchase_ts THEN 'DELIVERED_BEFORE_PURCHASE'
       WHEN status IN ('canceled', 'unavailable') AND delivered_ts IS NOT NULL THEN 'CANCELED_WITH_DELIVERY_DATE'
  END AS reason
FROM t;

DROP TABLE IF EXISTS staging.orders CASCADE;
CREATE TABLE staging.orders AS
SELECT order_id, customer_id, status, purchase_ts, approved_ts, carrier_ts, delivered_ts, estimated_ts,
       (approved_ts < purchase_ts)                     AS flag_approved_before_purchase,
       (carrier_ts > delivered_ts)                     AS flag_carrier_after_delivery,
       (status = 'delivered' AND delivered_ts IS NULL) AS flag_delivered_missing_date
FROM order_class WHERE reason IS NULL;
ALTER TABLE staging.orders ADD PRIMARY KEY (order_id);

INSERT INTO staging.quarantine(source_table, record_key, reason_code)
SELECT 'orders', order_id, reason FROM order_class WHERE reason IS NOT NULL;
