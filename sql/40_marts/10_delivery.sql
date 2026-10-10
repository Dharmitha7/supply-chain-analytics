-- Delivery KPIs. Definitions (see docs/assumptions.md):
--   on-time %        = delivered date <= estimated date / delivered orders that have both dates
--   fulfillment rate = delivered / all non-canceled orders        cancel rate = canceled / all orders
--   cycle time       = purchase -> delivered     carrier handoff = approved -> carrier pickup
-- Cohort month = order purchase month.
DROP VIEW IF EXISTS marts.kpi_delivery_monthly, marts.delivery_by_state, marts.review_vs_lateness CASCADE;

CREATE VIEW marts.kpi_delivery_monthly AS
SELECT date_trunc('month', d.date)::date AS month_start,
       d.year_month,
       count(*)                                                    AS orders,
       count(*) FILTER (WHERE s.is_canceled)                       AS canceled_orders,
       count(*) FILTER (WHERE NOT s.is_canceled)                   AS non_canceled_orders,
       count(*) FILTER (WHERE s.is_delivered)                      AS delivered_orders,
       count(*) FILTER (WHERE s.is_on_time IS NOT NULL)            AS otd_eligible_orders,
       count(*) FILTER (WHERE s.is_on_time)                        AS on_time_orders,
       round(100.0 * count(*) FILTER (WHERE s.is_on_time)
             / nullif(count(*) FILTER (WHERE s.is_on_time IS NOT NULL), 0), 2)     AS otd_pct,
       round(avg(s.days_late), 2)                                  AS avg_days_late,
       round(avg(s.days_late) FILTER (WHERE s.days_late > 0), 2)   AS avg_days_late_when_late,
       round(avg(s.cycle_days) FILTER (WHERE s.is_delivered), 2)   AS avg_cycle_days,
       round(avg(s.carrier_handoff_days), 2)                       AS avg_carrier_handoff_days,
       round(100.0 * count(*) FILTER (WHERE s.is_delivered)
             / nullif(count(*) FILTER (WHERE NOT s.is_canceled), 0), 2)            AS fulfillment_rate_pct,
       round(100.0 * count(*) FILTER (WHERE s.is_canceled) / count(*), 2)          AS cancel_rate_pct,
       round(avg(s.review_score), 2)                               AS avg_review_score
FROM core.fact_shipment s
JOIN core.dim_date d ON d.date_key = s.purchase_date_key
GROUP BY 1, 2;

CREATE VIEW marts.delivery_by_state AS
SELECT c.state,
       count(*)                                                    AS orders,
       count(*) FILTER (WHERE s.is_on_time IS NOT NULL)            AS otd_eligible_orders,
       round(100.0 * count(*) FILTER (WHERE s.is_on_time)
             / nullif(count(*) FILTER (WHERE s.is_on_time IS NOT NULL), 0), 2)     AS otd_pct,
       round(avg(s.days_late), 2)                                  AS avg_days_late,
       round(avg(s.cycle_days) FILTER (WHERE s.is_delivered), 2)   AS avg_cycle_days,
       round(avg(s.carrier_handoff_days), 2)                       AS avg_carrier_handoff_days,
       round(avg(s.review_score), 2)                               AS avg_review_score,
       avg(c.lat)                                                  AS lat,
       avg(c.lng)                                                  AS lng
FROM core.fact_shipment s
JOIN core.dim_customer c USING (customer_key)
GROUP BY c.state;

CREATE VIEW marts.review_vs_lateness AS
SELECT b.sort_order, b.bucket,
       count(*)                          AS orders,
       round(avg(s.review_score), 2)     AS avg_review_score
FROM core.fact_shipment s
CROSS JOIN LATERAL (SELECT CASE
        WHEN s.days_late = 0  THEN 1 WHEN s.days_late <= 3  THEN 2 WHEN s.days_late <= 7 THEN 3
        WHEN s.days_late <= 14 THEN 4 ELSE 5 END AS sort_order,
      CASE
        WHEN s.days_late = 0  THEN 'On time' WHEN s.days_late <= 3  THEN '1-3 days late'
        WHEN s.days_late <= 7 THEN '4-7 days late' WHEN s.days_late <= 14 THEN '8-14 days late'
        ELSE '15+ days late' END AS bucket) b
WHERE s.days_late IS NOT NULL AND s.review_score IS NOT NULL
GROUP BY b.sort_order, b.bucket;
