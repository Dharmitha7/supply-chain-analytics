-- Marts-layer checks (loaded after checks.sql, which truncates dq.check).
INSERT INTO dq.check (check_id, layer, table_name, check_type, description, severity, sql, threshold) VALUES
('MART_OTD_RANGE', 'marts', 'marts.kpi_delivery_monthly', 'kpi_sanity', 'monthly on-time pct within 0..100', 'critical',
 $q$SELECT count(*) FROM marts.kpi_delivery_monthly WHERE otd_pct < 0 OR otd_pct > 100$q$, 0),
('MART_RATES_RANGE', 'marts', 'marts.kpi_delivery_monthly', 'kpi_sanity', 'fulfillment and cancel rates within 0..100', 'critical',
 $q$SELECT count(*) FROM marts.kpi_delivery_monthly WHERE fulfillment_rate_pct NOT BETWEEN 0 AND 100 OR cancel_rate_pct NOT BETWEEN 0 AND 100$q$, 0),
('MART_STOCKOUT_RANGE', 'marts', 'marts.inventory_weekly', 'kpi_sanity', 'weekly stockout rate within 0..100', 'critical',
 $q$SELECT count(*) FROM marts.inventory_weekly WHERE stockout_rate_pct NOT BETWEEN 0 AND 100$q$, 0),
('MART_DAYS_OF_SUPPLY_NONNEG', 'marts', 'marts.inventory_position_current', 'range', 'days of supply never negative', 'critical',
 $q$SELECT count(*) FROM marts.inventory_position_current WHERE days_of_supply < 0$q$, 0),
('MART_SUPPLIER_UNIQUE', 'marts', 'marts.supplier_scorecard', 'uniqueness', 'one scorecard row per supplier', 'critical',
 $q$SELECT count(*) - count(DISTINCT supplier_key) FROM marts.supplier_scorecard$q$, 0),
('MART_SUPPLIER_COMPLETE', 'marts', 'marts.supplier_scorecard', 'reconciliation', 'scorecard covers every supplier', 'critical',
 $q$SELECT abs((SELECT count(*) FROM core.dim_supplier) - (SELECT count(*) FROM marts.supplier_scorecard))$q$, 0),
('MART_SHIPMENTS_RECON', 'marts', 'marts.shipments', 'reconciliation', 'marts.shipments = core.fact_shipment rows', 'critical',
 $q$SELECT abs((SELECT count(*) FROM core.fact_shipment) - (SELECT count(*) FROM marts.shipments))$q$, 0),
('MART_ORDERS_RECON', 'marts', 'marts.kpi_delivery_monthly', 'reconciliation', 'monthly order counts sum to fact_shipment', 'critical',
 $q$SELECT abs((SELECT count(*) FROM core.fact_shipment) - (SELECT COALESCE(sum(orders), 0) FROM marts.kpi_delivery_monthly))$q$, 0);
