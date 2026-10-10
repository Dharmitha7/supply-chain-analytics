-- Reviews: keep the latest review per order; invalid scores and orphans quarantined.
DROP TABLE IF EXISTS pg_temp.review_class;
CREATE TEMP TABLE review_class AS
WITH t AS (
  SELECT btrim(review_id) AS review_id, btrim(order_id) AS order_id,
         CASE WHEN review_score ~ '^[0-9]+$' THEN review_score::int END AS review_score,
         NULLIF(review_creation_date, '')::timestamp AS created_ts,
         NULLIF(review_answer_timestamp, '')::timestamp AS answered_ts
  FROM raw.order_reviews),
v AS (
  SELECT t.*,
    CASE WHEN review_id = '' OR order_id = '' THEN 'MISSING_KEY'
         WHEN review_score IS NULL OR review_score NOT BETWEEN 1 AND 5 THEN 'INVALID_SCORE'
         WHEN NOT EXISTS (SELECT 1 FROM staging.orders o WHERE o.order_id = t.order_id) THEN 'ORPHAN_ORDER'
    END AS reason
  FROM t)
SELECT v.*,
  CASE WHEN reason IS NOT NULL THEN reason
       WHEN row_number() OVER (PARTITION BY order_id
            ORDER BY (reason IS NULL) DESC, answered_ts DESC NULLS LAST, created_ts DESC, review_id) > 1
       THEN 'SUPERSEDED_DUPLICATE' END AS final_reason
FROM v;

DROP TABLE IF EXISTS staging.order_reviews CASCADE;
CREATE TABLE staging.order_reviews AS
SELECT review_id, order_id, review_score, created_ts, answered_ts FROM review_class WHERE final_reason IS NULL;
ALTER TABLE staging.order_reviews ADD PRIMARY KEY (order_id);

INSERT INTO staging.quarantine(source_table, record_key, reason_code)
SELECT 'order_reviews', review_id || '#' || order_id, final_reason FROM review_class WHERE final_reason IS NOT NULL;

-- Payments
DROP TABLE IF EXISTS pg_temp.payment_class;
CREATE TEMP TABLE payment_class AS
WITH t AS (
  SELECT btrim(order_id) AS order_id, NULLIF(payment_sequential, '')::int AS seq,
         lower(btrim(payment_type)) AS payment_type, NULLIF(payment_installments, '')::int AS installments,
         NULLIF(payment_value, '')::numeric(12,2) AS payment_value
  FROM raw.order_payments)
SELECT t.*,
  CASE WHEN order_id = '' OR seq IS NULL THEN 'MISSING_KEY'
       WHEN row_number() OVER (PARTITION BY order_id, seq ORDER BY payment_value) > 1 THEN 'DUPLICATE_KEY'
       WHEN payment_value IS NULL OR payment_value < 0 THEN 'INVALID_PAYMENT'
       WHEN NOT EXISTS (SELECT 1 FROM staging.orders o WHERE o.order_id = t.order_id) THEN 'ORPHAN_ORDER'
  END AS reason
FROM t;

DROP TABLE IF EXISTS staging.order_payments CASCADE;
CREATE TABLE staging.order_payments AS
SELECT order_id, seq, payment_type, installments, payment_value FROM payment_class WHERE reason IS NULL;
ALTER TABLE staging.order_payments ADD PRIMARY KEY (order_id, seq);

INSERT INTO staging.quarantine(source_table, record_key, reason_code)
SELECT 'order_payments', order_id || '#' || COALESCE(seq::text, ''), reason FROM payment_class WHERE reason IS NOT NULL;

-- Row-count audit trail (rows_in = raw, rows_out = kept in staging)
INSERT INTO dq.run_log(step, table_name, rows_in, rows_out) VALUES
 ('staging', 'staging.orders',         (SELECT count(*) FROM raw.orders),         (SELECT count(*) FROM staging.orders)),
 ('staging', 'staging.order_items',    (SELECT count(*) FROM raw.order_items),    (SELECT count(*) FROM staging.order_items)),
 ('staging', 'staging.order_reviews',  (SELECT count(*) FROM raw.order_reviews),  (SELECT count(*) FROM staging.order_reviews)),
 ('staging', 'staging.order_payments', (SELECT count(*) FROM raw.order_payments), (SELECT count(*) FROM staging.order_payments));
INSERT INTO dq.run_log(step, table_name, rows_in, rows_out)
SELECT 'quarantine', source_table || ':' || reason_code, count(*), count(*)
FROM staging.quarantine GROUP BY source_table, reason_code;
