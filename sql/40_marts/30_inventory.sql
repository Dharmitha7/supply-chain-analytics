-- Inventory KPIs. ALL values here derive from the SIMULATED inventory layer (demand itself is real Olist sales).
--   turnover        = COGS (units sold x unit cost) / average inventory value, trailing 52 weeks
--   days of supply  = on_hand / (trailing 13-week average daily demand)
--   stockout risk   = days of supply < supplier p90 lead time (and on_hand > 0);  overstock = days of supply > 120
-- "Current" = the latest simulated week, not now().
DROP VIEW IF EXISTS marts.inventory_position_current, marts.inventory_weekly CASCADE;

CREATE VIEW marts.inventory_position_current AS
WITH bounds AS (
  SELECT max(d.date) AS last_week FROM core.fact_inventory_snapshot i JOIN core.dim_date d ON d.date_key = i.snapshot_date_key),
snap AS (
  SELECT i.*, d.date AS snapshot_date FROM core.fact_inventory_snapshot i JOIN core.dim_date d ON d.date_key = i.snapshot_date_key),
hist AS (
  SELECT product_key, warehouse_key,
         count(*)                                      AS weeks_observed,
         count(*) FILTER (WHERE is_stockout)           AS stockout_weeks,
         sum(units_demand) - sum(units_sold)           AS lost_units,
         sum(units_demand)                             AS total_demand_units
  FROM snap GROUP BY product_key, warehouse_key),
trail13 AS (
  SELECT s.product_key, s.warehouse_key, sum(s.units_demand) AS demand_13w
  FROM snap s, bounds b WHERE s.snapshot_date > b.last_week - 91 GROUP BY 1, 2),
trail52 AS (
  SELECT s.product_key, s.warehouse_key,
         sum(s.units_sold * s.unit_cost)                         AS cogs_52w,
         avg(s.on_hand * s.unit_cost)                            AS avg_inventory_value_52w
  FROM snap s, bounds b WHERE s.snapshot_date > b.last_week - 364 GROUP BY 1, 2),
latest AS (
  SELECT s.* FROM snap s, bounds b WHERE s.snapshot_date = b.last_week),
joined AS (
  SELECT l.product_key, l.warehouse_key, l.supplier_key, l.snapshot_date,
         l.on_hand, l.on_order, l.reorder_point, l.safety_stock, l.unit_cost,
         round(l.on_hand * l.unit_cost, 2)                         AS inventory_value,
         COALESCE(t13.demand_13w, 0)                               AS demand_13w,
         round(COALESCE(t13.demand_13w, 0) / 13.0, 3)              AS avg_weekly_demand,
         CASE WHEN COALESCE(t13.demand_13w, 0) > 0
              THEN round(l.on_hand / (t13.demand_13w / 91.0), 1) END AS days_of_supply,
         lt.sim_lead_p90_days                                       AS supplier_lead_p90_days,
         round(t52.cogs_52w, 2)                                     AS cogs_52w,
         round(t52.avg_inventory_value_52w, 2)                      AS avg_inventory_value_52w,
         CASE WHEN t52.avg_inventory_value_52w > 0
              THEN round(t52.cogs_52w / t52.avg_inventory_value_52w, 2) END AS inventory_turnover_52w,
         h.weeks_observed, h.stockout_weeks,
         round(100.0 * h.stockout_weeks / nullif(h.weeks_observed, 0), 2)           AS stockout_week_pct,
         round(100.0 * (1 - h.lost_units::numeric / nullif(h.total_demand_units, 0)), 2) AS fill_rate_pct,
         l.on_hand = 0 AS is_stockout_now
  FROM latest l
  JOIN hist h USING (product_key, warehouse_key)
  LEFT JOIN trail13 t13 USING (product_key, warehouse_key)
  LEFT JOIN trail52 t52 USING (product_key, warehouse_key)
  LEFT JOIN marts.supplier_lead_time lt ON lt.supplier_key = l.supplier_key)
SELECT j.*,
       COALESCE(j.on_hand > 0 AND j.days_of_supply < j.supplier_lead_p90_days, FALSE) AS is_stockout_risk,
       COALESCE(j.days_of_supply > 120, FALSE)                               AS is_overstock,
       CASE WHEN j.on_hand = 0 THEN 'stockout'
            WHEN j.demand_13w = 0 THEN 'no_recent_demand'
            WHEN j.days_of_supply > 120 THEN 'overstock'
            WHEN j.supplier_lead_p90_days IS NOT NULL AND j.days_of_supply < j.supplier_lead_p90_days THEN 'at_risk'
            ELSE 'healthy' END                                              AS risk_status
FROM joined j;

CREATE VIEW marts.inventory_weekly AS
SELECT d.date AS week_start, d.year_month,
       count(*)                                                     AS series,
       count(*) FILTER (WHERE i.is_stockout)                        AS stockout_series,
       round(100.0 * count(*) FILTER (WHERE i.is_stockout) / count(*), 2) AS stockout_rate_pct,
       sum(i.units_demand)                                          AS units_demand,
       sum(i.units_sold)                                            AS units_sold,
       round(100.0 * sum(i.units_sold) / nullif(sum(i.units_demand), 0), 2) AS fill_rate_pct,
       round(sum(i.on_hand * i.unit_cost), 2)                       AS inventory_value,
       round(sum(i.units_sold * i.unit_cost), 2)                    AS cogs
FROM core.fact_inventory_snapshot i
JOIN core.dim_date d ON d.date_key = i.snapshot_date_key
GROUP BY d.date, d.year_month;
