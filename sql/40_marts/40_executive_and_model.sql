-- Executive KPIs + pass-through views so Power BI only ever connects to the marts schema.
DROP VIEW IF EXISTS marts.kpi_executive_monthly, marts.quarantine_summary, marts.dq_latest,
                    marts.dim_date, marts.dim_product, marts.dim_supplier, marts.dim_warehouse,
                    marts.shipments, marts.inventory_snapshot CASCADE;

-- Delivery (real) and inventory (simulated) side by side per month. Inventory columns are prefixed sim_.
CREATE VIEW marts.kpi_executive_monthly AS
WITH inv AS (
  SELECT year_month,
         sum(cogs)                                           AS cogs,
         avg(inventory_value)                                AS avg_inventory_value,
         round(avg(stockout_rate_pct), 2)                    AS stockout_rate_pct,
         round(100.0 * sum(units_sold) / nullif(sum(units_demand), 0), 2) AS fill_rate_pct
  FROM marts.inventory_weekly GROUP BY year_month)
SELECT d.month_start, d.year_month, d.orders, d.otd_pct, d.avg_days_late, d.avg_cycle_days,
       d.fulfillment_rate_pct, d.cancel_rate_pct, d.avg_review_score,
       i.stockout_rate_pct AS sim_stockout_rate_pct,
       i.fill_rate_pct     AS sim_fill_rate_pct,
       CASE WHEN i.avg_inventory_value > 0 THEN round(i.cogs * 12 / i.avg_inventory_value, 2) END AS sim_turnover_annualized
FROM marts.kpi_delivery_monthly d
LEFT JOIN inv i USING (year_month);

-- Model tables for Power BI (row level, so DAX measures can be defined in the report)
CREATE VIEW marts.dim_date      AS SELECT * FROM core.dim_date;
CREATE VIEW marts.dim_product   AS SELECT product_key, product_id, category_pt, category_en, weight_g, volume_cm3 FROM core.dim_product;
CREATE VIEW marts.dim_supplier  AS SELECT supplier_key, seller_id, city, state, lat, lng FROM core.dim_supplier;
CREATE VIEW marts.dim_warehouse AS SELECT * FROM core.dim_warehouse;

CREATE VIEW marts.shipments AS
SELECT s.order_id, s.purchase_date_key, c.state AS customer_state, s.warehouse_key, s.order_status,
       s.is_delivered, s.is_canceled, s.is_on_time, s.days_late, s.days_vs_estimate, s.cycle_days,
       s.carrier_handoff_days, s.ship_lead_days, s.review_score, s.n_items, s.order_value
FROM core.fact_shipment s JOIN core.dim_customer c USING (customer_key);

CREATE VIEW marts.inventory_snapshot AS
SELECT i.snapshot_date_key, i.product_key, i.warehouse_key, i.supplier_key, i.on_hand, i.on_order,
       i.reorder_point, i.safety_stock, i.units_demand, i.units_sold, i.unit_cost,
       i.on_hand * i.unit_cost AS inventory_value, i.units_sold * i.unit_cost AS cogs, i.is_stockout
FROM core.fact_inventory_snapshot i;

-- Data-quality page sources
CREATE VIEW marts.quarantine_summary AS
SELECT source_table, reason_code, count(*) AS rows_quarantined
FROM staging.quarantine GROUP BY source_table, reason_code;

CREATE VIEW marts.dq_latest AS
SELECT r.run_id, r.checked_at, r.layer, r.check_id, c.description, r.table_name, r.check_type, r.severity,
       r.value, r.threshold, r.passed, r.error,
       CASE WHEN r.passed THEN 'PASS' WHEN r.severity = 'critical' THEN 'FAIL' ELSE 'WARN' END AS status,
       CASE WHEN r.passed THEN 3 WHEN r.severity = 'critical' THEN 1 ELSE 2 END AS status_order   -- FAIL, WARN, PASS
FROM dq.result r
LEFT JOIN dq.check c USING (check_id)
WHERE r.run_id = (SELECT run_id FROM dq.result ORDER BY checked_at DESC, result_id DESC LIMIT 1);
