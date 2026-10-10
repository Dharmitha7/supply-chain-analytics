-- Order items: duplicates, invalid money values and orphan FKs go to quarantine.
DROP TABLE IF EXISTS pg_temp.item_class;
CREATE TEMP TABLE item_class AS
WITH t AS (
  SELECT btrim(order_id) AS order_id, NULLIF(order_item_id, '')::int AS item_no,
         btrim(product_id) AS product_id, btrim(seller_id) AS seller_id,
         NULLIF(shipping_limit_date, '')::timestamp AS shipping_limit_ts,
         NULLIF(price, '')::numeric(12,2) AS price, NULLIF(freight_value, '')::numeric(12,2) AS freight_value
  FROM raw.order_items)
SELECT t.*,
  CASE WHEN order_id = '' OR item_no IS NULL THEN 'MISSING_KEY'
       WHEN row_number() OVER (PARTITION BY order_id, item_no ORDER BY price) > 1 THEN 'DUPLICATE_KEY'
       WHEN price IS NULL OR price <= 0 THEN 'INVALID_PRICE'
       WHEN freight_value IS NULL OR freight_value < 0 THEN 'INVALID_FREIGHT'
       WHEN NOT EXISTS (SELECT 1 FROM staging.orders o WHERE o.order_id = t.order_id) THEN 'ORPHAN_ORDER'
       WHEN NOT EXISTS (SELECT 1 FROM staging.products p WHERE p.product_id = t.product_id) THEN 'ORPHAN_PRODUCT'
       WHEN NOT EXISTS (SELECT 1 FROM staging.sellers s WHERE s.seller_id = t.seller_id) THEN 'ORPHAN_SELLER'
  END AS reason
FROM t;

DROP TABLE IF EXISTS staging.order_items CASCADE;
CREATE TABLE staging.order_items AS
SELECT order_id, item_no, product_id, seller_id, shipping_limit_ts, price, freight_value
FROM item_class WHERE reason IS NULL;
ALTER TABLE staging.order_items ADD PRIMARY KEY (order_id, item_no);

INSERT INTO staging.quarantine(source_table, record_key, reason_code)
SELECT 'order_items', order_id || '#' || COALESCE(item_no::text, ''), reason FROM item_class WHERE reason IS NOT NULL;
