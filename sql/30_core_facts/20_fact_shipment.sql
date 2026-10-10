-- One row per order. Delivery timing metrics are derived here once so every mart uses the same definition.
-- On-time rule: delivered DATE <= estimated DATE (Olist estimates are date-only, midnight).
CREATE TABLE core.fact_shipment (
    order_id             TEXT PRIMARY KEY,
    customer_key         INT NOT NULL REFERENCES core.dim_customer,
    warehouse_key        INT NOT NULL REFERENCES core.dim_warehouse,
    purchase_date_key    INT NOT NULL REFERENCES core.dim_date,
    order_status         TEXT NOT NULL,
    purchase_ts          TIMESTAMP NOT NULL,
    approved_ts          TIMESTAMP,
    carrier_pickup_ts    TIMESTAMP,
    delivered_ts         TIMESTAMP,
    estimated_ts         TIMESTAMP,
    is_delivered         BOOLEAN NOT NULL,                 -- status delivered AND has a delivery date
    is_canceled          BOOLEAN NOT NULL,                 -- canceled or unavailable
    ship_lead_days       NUMERIC(8,2),                     -- purchase -> carrier pickup
    carrier_handoff_days NUMERIC(8,2),                     -- approved -> carrier pickup
    cycle_days           NUMERIC(8,2),                     -- purchase -> delivered
    days_vs_estimate     INT,                              -- delivered date - estimated date (negative = early)
    days_late            INT,                              -- max(days_vs_estimate, 0)
    is_on_time           BOOLEAN,                          -- NULL when not delivered
    review_score         INT,
    n_items              INT NOT NULL,
    order_value          NUMERIC(12,2) NOT NULL
);
INSERT INTO core.fact_shipment
SELECT o.order_id, c.customer_key, c.warehouse_key, to_char(o.purchase_ts, 'YYYYMMDD')::int, o.status,
       o.purchase_ts, o.approved_ts, o.carrier_ts, o.delivered_ts, o.estimated_ts,
       (o.status = 'delivered' AND o.delivered_ts IS NOT NULL),
       o.status IN ('canceled', 'unavailable'),
       round((extract(epoch FROM o.carrier_ts - o.purchase_ts) / 86400)::numeric, 2),
       round((extract(epoch FROM o.carrier_ts - o.approved_ts) / 86400)::numeric, 2),
       round((extract(epoch FROM o.delivered_ts - o.purchase_ts) / 86400)::numeric, 2),
       CASE WHEN o.status = 'delivered' AND o.delivered_ts IS NOT NULL AND o.estimated_ts IS NOT NULL
            THEN o.delivered_ts::date - o.estimated_ts::date END,
       CASE WHEN o.status = 'delivered' AND o.delivered_ts IS NOT NULL AND o.estimated_ts IS NOT NULL
            THEN greatest(o.delivered_ts::date - o.estimated_ts::date, 0) END,
       CASE WHEN o.status = 'delivered' AND o.delivered_ts IS NOT NULL AND o.estimated_ts IS NOT NULL
            THEN o.delivered_ts::date <= o.estimated_ts::date END,
       r.review_score,
       COALESCE(i.n_items, 0), COALESCE(i.order_value, 0)
FROM staging.orders o
JOIN core.dim_customer c ON c.customer_id = o.customer_id
LEFT JOIN staging.order_reviews r ON r.order_id = o.order_id
LEFT JOIN (SELECT order_id, count(*) AS n_items, sum(price) AS order_value
           FROM staging.order_items GROUP BY order_id) i ON i.order_id = o.order_id;

CREATE INDEX ON core.fact_shipment (purchase_date_key);

INSERT INTO dq.run_log(step, table_name, rows_in, rows_out) VALUES
 ('core', 'core.fact_order_line', (SELECT count(*) FROM staging.order_items), (SELECT count(*) FROM core.fact_order_line)),
 ('core', 'core.fact_shipment',   (SELECT count(*) FROM staging.orders),      (SELECT count(*) FROM core.fact_shipment));
