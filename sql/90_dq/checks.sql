-- Data-quality check definitions. Each `sql` returns ONE number (violation count or metric);
-- the check passes when value <= threshold. critical failures stop the pipeline; warn failures are reported.
-- Reloaded on every run so this file is the single source of truth.
TRUNCATE dq.check;

INSERT INTO dq.check (check_id, layer, table_name, check_type, description, severity, sql, threshold) VALUES
-- ===================== RAW =====================
('RAW_ORDERS_NOT_EMPTY', 'raw', 'raw.orders', 'row_count', 'raw.orders has rows', 'critical',
 $q$SELECT CASE WHEN count(*) > 0 THEN 0 ELSE 1 END FROM raw.orders$q$, 0),
('RAW_ITEMS_NOT_EMPTY', 'raw', 'raw.order_items', 'row_count', 'raw.order_items has rows', 'critical',
 $q$SELECT CASE WHEN count(*) > 0 THEN 0 ELSE 1 END FROM raw.order_items$q$, 0),
('RAW_FRESHNESS_INGEST', 'raw', 'dq.run_log', 'freshness', 'Hours since the last raw ingest (threshold 48h)', 'warn',
 $q$SELECT COALESCE(round(extract(epoch FROM now() - max(logged_at)) / 3600), 9999) FROM dq.run_log WHERE step = 'ingest'$q$, 48),

-- ===================== STAGING =====================
('STG_ORDERS_PK_UNIQUE', 'staging', 'staging.orders', 'uniqueness', 'order_id is unique', 'critical',
 $q$SELECT count(*) - count(DISTINCT order_id) FROM staging.orders$q$, 0),
('STG_ITEMS_PK_UNIQUE', 'staging', 'staging.order_items', 'uniqueness', '(order_id, item_no) is unique', 'critical',
 $q$SELECT count(*) - count(DISTINCT (order_id, item_no)) FROM staging.order_items$q$, 0),
('STG_CUSTOMERS_PK_UNIQUE', 'staging', 'staging.customers', 'uniqueness', 'customer_id is unique', 'critical',
 $q$SELECT count(*) - count(DISTINCT customer_id) FROM staging.customers$q$, 0),
('STG_ORDERS_NOT_NULL_KEYS', 'staging', 'staging.orders', 'not_null', 'order_id, customer_id, purchase_ts are not null', 'critical',
 $q$SELECT count(*) FROM staging.orders WHERE order_id IS NULL OR customer_id IS NULL OR purchase_ts IS NULL$q$, 0),
('STG_ORDERS_STATUS_ACCEPTED', 'staging', 'staging.orders', 'accepted_values', 'order status is a known value', 'critical',
 $q$SELECT count(*) FROM staging.orders WHERE status NOT IN
    ('created','approved','invoiced','processing','shipped','delivered','unavailable','canceled')$q$, 0),
('STG_ITEMS_ORPHAN_ORDER', 'staging', 'staging.order_items', 'referential', 'every item has a staged order', 'critical',
 $q$SELECT count(*) FROM staging.order_items i LEFT JOIN staging.orders o USING (order_id) WHERE o.order_id IS NULL$q$, 0),
('STG_ITEMS_ORPHAN_PRODUCT', 'staging', 'staging.order_items', 'referential', 'every item has a staged product', 'critical',
 $q$SELECT count(*) FROM staging.order_items i LEFT JOIN staging.products p USING (product_id) WHERE p.product_id IS NULL$q$, 0),
('STG_ITEMS_ORPHAN_SELLER', 'staging', 'staging.order_items', 'referential', 'every item has a staged seller', 'critical',
 $q$SELECT count(*) FROM staging.order_items i LEFT JOIN staging.sellers s USING (seller_id) WHERE s.seller_id IS NULL$q$, 0),
('STG_ORDERS_ORPHAN_CUSTOMER', 'staging', 'staging.orders', 'referential', 'every order has a staged customer', 'critical',
 $q$SELECT count(*) FROM staging.orders o LEFT JOIN staging.customers c USING (customer_id) WHERE c.customer_id IS NULL$q$, 0),
('STG_ITEMS_PRICE_POSITIVE', 'staging', 'staging.order_items', 'range', 'price > 0', 'critical',
 $q$SELECT count(*) FROM staging.order_items WHERE price IS NULL OR price <= 0$q$, 0),
('STG_ITEMS_FREIGHT_NONNEG', 'staging', 'staging.order_items', 'range', 'freight_value >= 0', 'critical',
 $q$SELECT count(*) FROM staging.order_items WHERE freight_value IS NULL OR freight_value < 0$q$, 0),
('STG_REVIEW_SCORE_RANGE', 'staging', 'staging.order_reviews', 'range', 'review score between 1 and 5', 'critical',
 $q$SELECT count(*) FROM staging.order_reviews WHERE review_score NOT BETWEEN 1 AND 5$q$, 0),
('STG_DATE_DELIVERED_AFTER_PURCHASE', 'staging', 'staging.orders', 'date_logic', 'delivered_ts >= purchase_ts', 'critical',
 $q$SELECT count(*) FROM staging.orders WHERE delivered_ts < purchase_ts$q$, 0),
('STG_CANCELED_NO_DELIVERY', 'staging', 'staging.orders', 'date_logic', 'canceled/unavailable orders have no delivery date', 'critical',
 $q$SELECT count(*) FROM staging.orders WHERE status IN ('canceled','unavailable') AND delivered_ts IS NOT NULL$q$, 0),
('STG_DATE_APPROVED_BEFORE_PURCHASE', 'staging', 'staging.orders', 'date_logic', 'orders approved before purchase (flagged, kept)', 'warn',
 $q$SELECT count(*) FROM staging.orders WHERE flag_approved_before_purchase$q$, 0),
('STG_DATE_CARRIER_AFTER_DELIVERY', 'staging', 'staging.orders', 'date_logic', 'carrier pickup after customer delivery (flagged, kept)', 'warn',
 $q$SELECT count(*) FROM staging.orders WHERE flag_carrier_after_delivery$q$, 0),
('STG_DELIVERED_MISSING_DATE', 'staging', 'staging.orders', 'not_null', 'delivered orders missing delivery date (flagged, kept)', 'warn',
 $q$SELECT count(*) FROM staging.orders WHERE flag_delivered_missing_date$q$, 0),
('STG_QUARANTINE_RATE_ITEMS', 'staging', 'staging.quarantine', 'volume', 'Percent of raw order_items quarantined (threshold 2)', 'warn',
 $q$SELECT round(100.0 * (SELECT count(*) FROM staging.quarantine WHERE source_table = 'order_items')
          / greatest((SELECT count(*) FROM raw.order_items), 1), 2)$q$, 2),
('STG_QUARANTINE_RATE_ORDERS', 'staging', 'staging.quarantine', 'volume', 'Percent of raw orders quarantined (threshold 2)', 'warn',
 $q$SELECT round(100.0 * (SELECT count(*) FROM staging.quarantine WHERE source_table = 'orders')
          / greatest((SELECT count(*) FROM raw.orders), 1), 2)$q$, 2),
('STG_RECON_ORDERS', 'staging', 'staging.orders', 'reconciliation', 'raw = staged + quarantined (orders)', 'critical',
 $q$SELECT abs((SELECT count(*) FROM raw.orders) - (SELECT count(*) FROM staging.orders)
             - (SELECT count(*) FROM staging.quarantine WHERE source_table = 'orders'))$q$, 0),
('STG_RECON_ORDER_ITEMS', 'staging', 'staging.order_items', 'reconciliation', 'raw = staged + quarantined (order_items)', 'critical',
 $q$SELECT abs((SELECT count(*) FROM raw.order_items) - (SELECT count(*) FROM staging.order_items)
             - (SELECT count(*) FROM staging.quarantine WHERE source_table = 'order_items'))$q$, 0),
('STG_RECON_ORDER_REVIEWS', 'staging', 'staging.order_reviews', 'reconciliation', 'raw = staged + quarantined (order_reviews)', 'critical',
 $q$SELECT abs((SELECT count(*) FROM raw.order_reviews) - (SELECT count(*) FROM staging.order_reviews)
             - (SELECT count(*) FROM staging.quarantine WHERE source_table = 'order_reviews'))$q$, 0),
('STG_RECON_ORDER_PAYMENTS', 'staging', 'staging.order_payments', 'reconciliation', 'raw = staged + quarantined (order_payments)', 'critical',
 $q$SELECT abs((SELECT count(*) FROM raw.order_payments) - (SELECT count(*) FROM staging.order_payments)
             - (SELECT count(*) FROM staging.quarantine WHERE source_table = 'order_payments'))$q$, 0),
('STG_RECON_CUSTOMERS', 'staging', 'staging.customers', 'reconciliation', 'raw = staged + quarantined (customers)', 'critical',
 $q$SELECT abs((SELECT count(*) FROM raw.customers) - (SELECT count(*) FROM staging.customers)
             - (SELECT count(*) FROM staging.quarantine WHERE source_table = 'customers'))$q$, 0),
('STG_RECON_SELLERS', 'staging', 'staging.sellers', 'reconciliation', 'raw = staged + quarantined (sellers)', 'critical',
 $q$SELECT abs((SELECT count(*) FROM raw.sellers) - (SELECT count(*) FROM staging.sellers)
             - (SELECT count(*) FROM staging.quarantine WHERE source_table = 'sellers'))$q$, 0),
('STG_RECON_PRODUCTS', 'staging', 'staging.products', 'reconciliation', 'raw = staged + quarantined (products)', 'critical',
 $q$SELECT abs((SELECT count(*) FROM raw.products) - (SELECT count(*) FROM staging.products)
             - (SELECT count(*) FROM staging.quarantine WHERE source_table = 'products'))$q$, 0),

-- ===================== CORE =====================
('CORE_RECON_ORDER_LINES', 'core', 'core.fact_order_line', 'reconciliation', 'staged items = fact_order_line rows', 'critical',
 $q$SELECT abs((SELECT count(*) FROM staging.order_items) - (SELECT count(*) FROM core.fact_order_line))$q$, 0),
('CORE_RECON_SHIPMENTS', 'core', 'core.fact_shipment', 'reconciliation', 'staged orders = fact_shipment rows', 'critical',
 $q$SELECT abs((SELECT count(*) FROM staging.orders) - (SELECT count(*) FROM core.fact_shipment))$q$, 0),
('CORE_RECON_REVENUE', 'core', 'core.fact_order_line', 'reconciliation', 'sum(price) staged = core', 'critical',
 $q$SELECT abs((SELECT COALESCE(sum(price), 0) FROM staging.order_items) - (SELECT COALESCE(sum(price), 0) FROM core.fact_order_line))$q$, 0),
('CORE_SHIPMENT_PK_UNIQUE', 'core', 'core.fact_shipment', 'uniqueness', 'one shipment row per order', 'critical',
 $q$SELECT count(*) - count(DISTINCT order_id) FROM core.fact_shipment$q$, 0),
('CORE_DIM_PRODUCT_UNIQUE', 'core', 'core.dim_product', 'uniqueness', 'product_id unique in dim_product', 'critical',
 $q$SELECT count(*) - count(DISTINCT product_id) FROM core.dim_product$q$, 0),
('CORE_LINE_FK_PRODUCT', 'core', 'core.fact_order_line', 'referential', 'order lines resolve to dim_product', 'critical',
 $q$SELECT count(*) FROM core.fact_order_line f LEFT JOIN core.dim_product d USING (product_key) WHERE d.product_key IS NULL$q$, 0),
('CORE_LINE_FK_SUPPLIER', 'core', 'core.fact_order_line', 'referential', 'order lines resolve to dim_supplier', 'critical',
 $q$SELECT count(*) FROM core.fact_order_line f LEFT JOIN core.dim_supplier d USING (supplier_key) WHERE d.supplier_key IS NULL$q$, 0),
('CORE_LINE_FK_CUSTOMER', 'core', 'core.fact_order_line', 'referential', 'order lines resolve to dim_customer', 'critical',
 $q$SELECT count(*) FROM core.fact_order_line f LEFT JOIN core.dim_customer d USING (customer_key) WHERE d.customer_key IS NULL$q$, 0),
('CORE_LINE_FK_DATE', 'core', 'core.fact_order_line', 'referential', 'order lines resolve to dim_date', 'critical',
 $q$SELECT count(*) FROM core.fact_order_line f LEFT JOIN core.dim_date d ON d.date_key = f.purchase_date_key WHERE d.date_key IS NULL$q$, 0),
('CORE_SHIPMENT_FK_CUSTOMER', 'core', 'core.fact_shipment', 'referential', 'shipments resolve to dim_customer', 'critical',
 $q$SELECT count(*) FROM core.fact_shipment f LEFT JOIN core.dim_customer d USING (customer_key) WHERE d.customer_key IS NULL$q$, 0),
('CORE_SHIPMENT_DAYS_LATE_NONNEG', 'core', 'core.fact_shipment', 'range', 'days_late >= 0', 'critical',
 $q$SELECT count(*) FROM core.fact_shipment WHERE days_late < 0$q$, 0),
('CORE_PO_RECEIPT_AFTER_ORDER', 'core', 'core.fact_purchase_order', 'date_logic', 'PO receipt_date after order date', 'critical',
 $q$SELECT count(*) FROM core.fact_purchase_order po JOIN core.dim_date d ON d.date_key = po.order_date_key WHERE po.receipt_date <= d.date$q$, 0),
('CORE_INV_NONNEG', 'core', 'core.fact_inventory_snapshot', 'range', 'on_hand and on_order never negative', 'critical',
 $q$SELECT count(*) FROM core.fact_inventory_snapshot WHERE on_hand < 0 OR on_order < 0$q$, 0),
('CORE_INV_SOLD_LE_DEMAND', 'core', 'core.fact_inventory_snapshot', 'range', 'units_sold <= units_demand', 'critical',
 $q$SELECT count(*) FROM core.fact_inventory_snapshot WHERE units_sold > units_demand$q$, 0),
('CORE_SIM_FLAGGED', 'core', 'core.fact_inventory_snapshot', 'labeling', 'every simulated row carries is_simulated = TRUE', 'critical',
 $q$SELECT (SELECT count(*) FROM core.fact_inventory_snapshot WHERE NOT is_simulated)
         + (SELECT count(*) FROM core.fact_purchase_order WHERE NOT is_simulated)$q$, 0),
('CORE_KPI_OTD_RANGE', 'core', 'core.fact_shipment', 'kpi_sanity', 'On-time pct lies within 0..100', 'critical',
 $q$SELECT CASE WHEN COALESCE(100.0 * avg(is_on_time::int), 0) BETWEEN 0 AND 100 THEN 0 ELSE 1 END FROM core.fact_shipment$q$, 0),
('CORE_KPI_OTD_PLAUSIBLE', 'core', 'core.fact_shipment', 'kpi_sanity', 'On-time pct between 50 and 100 (plausibility)', 'warn',
 $q$SELECT CASE WHEN COALESCE(100.0 * avg(is_on_time::int), 100) BETWEEN 50 AND 100 THEN 0 ELSE 1 END FROM core.fact_shipment$q$, 0);
