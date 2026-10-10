-- Supplier scorecard. delivery_* columns are REAL Olist outcomes for orders the seller shipped;
-- sim_* columns come from the SIMULATED purchase-order layer (replenishment lead times).
DROP VIEW IF EXISTS marts.supplier_lead_time, marts.supplier_scorecard CASCADE;

-- Lead-time statistics over RECEIVED simulated POs (in-transit POs are right-censored and excluded).
CREATE VIEW marts.supplier_lead_time AS
SELECT supplier_key,
       count(*)                                                       AS sim_po_count,
       round(avg(lead_days), 2)                                       AS sim_lead_mean_days,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY lead_days)         AS sim_lead_p90_days,
       round(stddev_samp(lead_days), 2)                               AS sim_lead_stddev_days,
       round(100.0 * avg(is_on_time::int), 2)                         AS sim_po_on_time_pct
FROM core.fact_purchase_order
WHERE is_received
GROUP BY supplier_key;

CREATE VIEW marts.supplier_scorecard AS
WITH lines AS (
  SELECT supplier_key, count(*) AS order_lines, count(DISTINCT order_id) AS orders, sum(price) AS revenue
  FROM core.fact_order_line GROUP BY supplier_key),
ship AS (
  SELECT o.supplier_key,
         count(*) FILTER (WHERE s.is_on_time IS NOT NULL) AS delivery_eligible_orders,
         round(100.0 * count(*) FILTER (WHERE s.is_on_time)
               / nullif(count(*) FILTER (WHERE s.is_on_time IS NOT NULL), 0), 2) AS delivery_otd_pct,
         round(avg(s.days_late), 2)        AS delivery_avg_days_late,
         round(avg(s.review_score), 2)     AS avg_review_score
  FROM (SELECT DISTINCT supplier_key, order_id FROM core.fact_order_line) o
  JOIN core.fact_shipment s USING (order_id)
  GROUP BY o.supplier_key),
base AS (
  SELECT d.supplier_key, d.seller_id, d.city, d.state,
         COALESCE(l.order_lines, 0) AS order_lines, COALESCE(l.orders, 0) AS orders, COALESCE(l.revenue, 0) AS revenue,
         sh.delivery_eligible_orders, sh.delivery_otd_pct, sh.delivery_avg_days_late, sh.avg_review_score,
         lt.sim_po_count, lt.sim_lead_mean_days, lt.sim_lead_p90_days, lt.sim_lead_stddev_days, lt.sim_po_on_time_pct
  FROM core.dim_supplier d
  LEFT JOIN lines l USING (supplier_key)
  LEFT JOIN ship sh USING (supplier_key)
  LEFT JOIN marts.supplier_lead_time lt USING (supplier_key)),
ranked AS (   -- only suppliers with >= 5 received POs are ranked: fewer is too noisy
  SELECT supplier_key,
         rank() OVER (ORDER BY sim_po_on_time_pct DESC, sim_lead_stddev_days ASC NULLS LAST, supplier_key) AS reliability_rank
  FROM base WHERE sim_po_count >= 5)
SELECT b.*, r.reliability_rank
FROM base b LEFT JOIN ranked r USING (supplier_key);
