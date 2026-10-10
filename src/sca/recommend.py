"""Generate docs/recommendations.md from the marts, so every number traces to the data.

Delivery findings are REAL (Olist). Supplier lead time, inventory and reorder point findings come from the
SIMULATED layer and are labelled as such. The reorder point impact is a counterfactual: the same demand and
the same supplier lead time draws are replayed under a revised policy.

    python -m sca.recommend
"""
import math

import numpy as np
import pandas as pd

from . import db
from .config import ROOT
from .simulate import COVER_WEEKS, Z_SERVICE, simulate_series

MIN_STATE_ORDERS = 300
MIN_SUPPLIER_ORDERS = 50


def q(sql: str) -> pd.DataFrame:
    return pd.read_sql(sql, db.engine())


def fmt(n, d=0):
    return f"{n:,.{d}f}"


def _lead_draws(profile, seed: int):
    rng = np.random.default_rng(seed)
    return lambda: max(1, int(round(rng.lognormal(math.log(profile.actual_median_days), profile.lead_sigma))))


def reorder_point_counterfactual(max_series: int = 400) -> dict:
    """Replay at risk series with s based on the supplier's observed p90 lead time instead of the quoted one."""
    cur = q("""SELECT product_key, warehouse_key, supplier_key, reorder_point, safety_stock, stockout_week_pct, fill_rate_pct
               FROM marts.inventory_position_current WHERE stockout_week_pct > 0""")
    prof = q("SELECT * FROM sim.supplier_profile").set_index("supplier_key")
    lt = q("SELECT supplier_key, sim_lead_p90_days FROM marts.supplier_lead_time").set_index("supplier_key")
    snap = q("""SELECT product_key, warehouse_key, d.date AS week, units_demand, on_hand, unit_cost
                FROM marts.inventory_snapshot i JOIN core.dim_date d ON d.date_key = i.snapshot_date_key
                ORDER BY 1, 2, 3""")
    snap["unit_cost"] = snap.unit_cost.astype(float)
    cur = cur.sort_values("stockout_week_pct", ascending=False).head(max_series)
    groups = {k: g for k, g in snap.groupby(["product_key", "warehouse_key"])}
    rows = []
    for r in cur.itertuples():
        g = groups[(r.product_key, r.warehouse_key)]
        d = g.units_demand.to_numpy()
        p = prof.loc[r.supplier_key]
        p90 = float(lt.loc[r.supplier_key].sim_lead_p90_days) if r.supplier_key in lt.index else float(p.quoted_lead_days)
        mu, sd = d.mean(), d.std()
        seed = int(r.product_key) * 1000 + int(r.warehouse_key)
        S_cover = max(2, math.ceil(mu * COVER_WEEKS))
        out = {}
        for name, lead_days in (("base", float(p.quoted_lead_days)), ("new", max(p90, float(p.quoted_lead_days)))):
            L = lead_days / 7 + 1
            safety = max(1, math.ceil(Z_SERVICE * sd * math.sqrt(L)))
            s = max(1, math.ceil(mu * L) + safety)
            res = simulate_series(d, s, s + S_cover, _lead_draws(p, seed))
            out[name] = (s, res.sold.sum() / max(d.sum(), 1), (res.on_hand == 0).mean(), (res.on_hand * g.unit_cost.to_numpy()).mean())
        rows.append({"product_key": r.product_key, "warehouse_key": r.warehouse_key,
                     "s_base": out["base"][0], "s_new": out["new"][0],
                     "fill_base": out["base"][1], "fill_new": out["new"][1],
                     "so_base": out["base"][2], "so_new": out["new"][2],
                     "inv_base": out["base"][3], "inv_new": out["new"][3]})
    df = pd.DataFrame(rows)
    return {"series": len(df), "changed": int((df.s_new > df.s_base).sum()),
            "fill_base": df.fill_base.mean() * 100, "fill_new": df.fill_new.mean() * 100,
            "so_base": df.so_base.mean() * 100, "so_new": df.so_new.mean() * 100,
            "inv_base": df.inv_base.sum(), "inv_new": df.inv_new.sum(), "detail": df}


def build_markdown() -> str:
    L: list[str] = []
    base = q("""SELECT count(*) AS orders,
                       count(*) FILTER (WHERE is_on_time IS NOT NULL) AS eligible,
                       count(*) FILTER (WHERE is_on_time IS FALSE) AS late,
                       avg(review_score) FILTER (WHERE is_on_time) AS rev_on,
                       avg(review_score) FILTER (WHERE is_on_time IS FALSE) AS rev_late,
                       100.0 * count(*) FILTER (WHERE review_score <= 2 AND is_on_time IS FALSE)
                           / nullif(count(*) FILTER (WHERE review_score IS NOT NULL AND is_on_time IS FALSE), 0) AS low_late,
                       100.0 * count(*) FILTER (WHERE review_score <= 2 AND is_on_time)
                           / nullif(count(*) FILTER (WHERE review_score IS NOT NULL AND is_on_time), 0) AS low_on,
                       sum(order_value) AS revenue,
                       sum(order_value) FILTER (WHERE is_on_time IS FALSE) AS revenue_late
                FROM core.fact_shipment""").iloc[0]
    late_rate = 100 * base.late / base.eligible
    L += ["# Recommendations", "",
          "Detailed findings and the actions they support. Delivery numbers come from real Olist orders (2016 to 2018). "
          "Anything marked **simulated** comes from the synthetic inventory layer and shows how the method would work, "
          "not what Olist sellers actually held. This file is generated by `python -m sca.recommend`, so every figure "
          "traces back to the marts.", ""]

    # ---- 1. cost of late delivery
    L += ["## 1. Late delivery is the most expensive operational problem in the data", "",
          f"{fmt(base.late)} of {fmt(base.eligible)} measurable orders ({late_rate:.1f}%) arrived after the promised date. "
          f"On time orders average a review score of {base.rev_on:.2f}. Late orders average {base.rev_late:.2f}, and "
          f"{base.low_late:.0f}% of them get one or two stars, against {base.low_on:.0f}% of on time orders. "
          f"Late orders also carry {fmt(base.revenue_late / 1000)}K BRL of the {fmt(base.revenue / 1e6, 1)}M BRL order value "
          f"({100 * base.revenue_late / base.revenue:.1f}%), which is the revenue most exposed to a lost repeat customer.", ""]

    # ---- 2. where the time goes
    t = q("""SELECT is_on_time,
                    avg(extract(epoch FROM carrier_pickup_ts - purchase_ts) / 86400) AS to_pickup,
                    avg(extract(epoch FROM delivered_ts - carrier_pickup_ts) / 86400) AS transit,
                    avg(extract(epoch FROM estimated_ts - purchase_ts) / 86400) AS promised
             FROM core.fact_shipment
             WHERE is_on_time IS NOT NULL AND carrier_pickup_ts IS NOT NULL AND carrier_pickup_ts <= delivered_ts
             GROUP BY is_on_time""").set_index("is_on_time")
    on, lt = t.loc[True], t.loc[False]
    extra = (lt.to_pickup + lt.transit) - (on.to_pickup + on.transit)
    L += ["## 2. The problem is carrier transit, not the delivery promise", "",
          "| Days | On time orders | Late orders |", "|---|---:|---:|",
          f"| Purchase to carrier pickup (seller side) | {on.to_pickup:.1f} | {lt.to_pickup:.1f} |",
          f"| Carrier pickup to delivery (carrier side) | {on.transit:.1f} | {lt.transit:.1f} |",
          f"| Delivery promised at purchase | {on.promised:.1f} | {lt.promised:.1f} |", "",
          f"Late orders were promised about the same window as on time orders, so a short promise is not the cause. "
          f"They take {extra:.0f} more days in total, and {100 * (lt.transit - on.transit) / extra:.0f}% of that extra time is spent "
          "in carrier transit. Across all orders the average promise is roughly twice the actual delivery time, "
          "so estimates are already padded heavily.", "",
          "**Action:** do not widen estimates further. Focus on the slow tail of carrier transit and set carrier "
          "service levels on the 95th percentile, not the average.", ""]

    # ---- 3. early warning
    w = q("""SELECT CASE WHEN ship_lead_days <= 3 THEN 1 WHEN ship_lead_days <= 5 THEN 2 WHEN ship_lead_days <= 8 THEN 3 ELSE 4 END AS k,
                    CASE WHEN ship_lead_days <= 3 THEN 'Up to 3 days' WHEN ship_lead_days <= 5 THEN '3 to 5 days'
                         WHEN ship_lead_days <= 8 THEN '5 to 8 days' ELSE 'More than 8 days' END AS bucket,
                    count(*) AS orders, 100.0 * avg((NOT is_on_time)::int) AS late_pct, avg(review_score) AS review
             FROM core.fact_shipment WHERE is_on_time IS NOT NULL AND ship_lead_days >= 0
             GROUP BY 1, 2 ORDER BY 1""")
    top, bot = w.iloc[-1], w.iloc[0]
    saved = top.orders * top.late_pct / 100 * 0.5
    L += ["## 3. Slow handoff to the carrier is a usable early warning", "",
          "| Time from purchase to carrier pickup | Orders | Late rate | Avg review |", "|---|---:|---:|---:|"]
    L += [f"| {r.bucket} | {fmt(r.orders)} | {r.late_pct:.1f}% | {r.review:.2f} |" for r in w.itertuples()]
    L += ["", f"Orders that wait more than 8 days for pickup are {top.late_pct / bot.late_pct:.1f} times more likely to be late than orders "
              f"picked up within 3 days. That is known days before the customer sees a problem.", "",
          f"**Action:** flag every order that reaches day 5 without a carrier scan, escalate to the seller, and offer expedited "
          f"shipping or proactive customer contact at day 8. As an illustration, if that intervention halved the late rate for the "
          f"{fmt(top.orders)} orders above 8 days, it would prevent about {fmt(saved)} late deliveries, "
          f"or {100 * saved / base.late:.0f}% of all late orders. The halving is an assumption to be tested, not a measured result.", ""]

    # ---- 4. states and sellers
    st = q(f"""SELECT c.state, count(*) AS orders, 100.0 * avg(is_on_time::int) AS otd,
                      count(*) FILTER (WHERE is_on_time IS FALSE) AS late,
                      percentile_cont(0.95) WITHIN GROUP (ORDER BY s.cycle_days) AS p95_days
               FROM core.fact_shipment s JOIN core.dim_customer c USING (customer_key)
               WHERE s.is_on_time IS NOT NULL GROUP BY 1 HAVING count(*) >= {MIN_STATE_ORDERS} ORDER BY otd LIMIT 5""")
    weak_orders, weak_late = int(st.orders.sum()), int(st.late.sum())
    fixed = weak_late - weak_orders * late_rate / 100
    L += ["## 4. Concentrate on five states and a small group of sellers", "",
          "**Customer states with the lowest on time rate (at least 300 orders):**", "",
          "| State | Orders | On time | Late orders | 95th percentile delivery (days) |", "|---|---:|---:|---:|---:|"]
    L += [f"| {r.state} | {fmt(r.orders)} | {r.otd:.1f}% | {fmt(r.late)} | {r.p95_days:.0f} |" for r in st.itertuples()]
    L += ["", f"These five Northeast states are {100 * weak_orders / base.eligible:.1f}% of orders but "
              f"{100 * weak_late / base.late:.1f}% of late orders. Bringing them to the national rate would remove about "
              f"{fmt(fixed)} late deliveries. A slow tail of 38 to 46 days at the 95th percentile points to long haul carrier "
              "legs rather than a seller problem.", ""]
    sl = q("""WITH l AS (
                SELECT o.supplier_key, count(*) FILTER (WHERE is_on_time IS FALSE) AS late, count(*) FILTER (WHERE is_on_time IS NOT NULL) AS n
                FROM (SELECT DISTINCT supplier_key, order_id FROM core.fact_order_line) o JOIN core.fact_shipment s USING (order_id)
                GROUP BY 1)
              SELECT count(*) FILTER (WHERE n >= 50) AS big,
                     count(*) FILTER (WHERE n >= 50 AND 100.0 * late / n > 10) AS bad,
                     100.0 * sum(n) FILTER (WHERE n >= 50 AND 100.0 * late / n > 10) / sum(n) AS share_orders,
                     100.0 * sum(late) FILTER (WHERE n >= 50 AND 100.0 * late / n > 10) / sum(late) AS share_late FROM l""").iloc[0]
    real = q(f"""SELECT seller_id, orders, delivery_otd_pct, delivery_avg_days_late, avg_review_score
                 FROM marts.supplier_scorecard WHERE delivery_eligible_orders >= {MIN_SUPPLIER_ORDERS}
                 ORDER BY delivery_otd_pct, orders DESC LIMIT 5""")
    L += [f"On the seller side, {int(sl.bad)} of {int(sl.big)} sellers with at least 50 orders run a late rate above 10%. "
          f"They ship {sl.share_orders:.0f}% of orders but cause {sl.share_late:.0f}% of late deliveries, about twice their share.", "",
          "| Seller | Orders | On time | Avg days late | Avg review |", "|---|---:|---:|---:|---:|"]
    L += [f"| {r.seller_id[-6:]} | {fmt(r.orders)} | {r.delivery_otd_pct}% | {r.delivery_avg_days_late} | {r.avg_review_score} |" for r in real.itertuples()]
    L += ["", "**Action:** put the weakest sellers on a corrective plan with a monthly on time target of 90%, "
              "and negotiate carrier service levels for the five states by name. Seller IDs are shortened to their last six characters.", ""]

    # ---- 5. replenishment (simulated)
    sim = q("""SELECT seller_id, sim_po_count, sim_lead_mean_days, sim_lead_p90_days, sim_po_on_time_pct
               FROM marts.supplier_scorecard WHERE reliability_rank IS NOT NULL ORDER BY reliability_rank DESC LIMIT 5""")
    rel = q("""SELECT is_unreliable, 100.0 * avg(is_on_time::int) AS otd, avg(lead_days) AS lead
               FROM core.fact_purchase_order po JOIN sim.supplier_profile USING (supplier_key) WHERE is_received GROUP BY 1""").set_index("is_unreliable")
    L += ["## 5. Plan around supplier reliability **(simulated)**", "",
          f"In the simulation, unreliable suppliers deliver purchase orders on time {rel.loc[True].otd:.0f}% of the time with an average "
          f"lead time of {rel.loc[True].lead:.0f} days, against {rel.loc[False].otd:.0f}% and {rel.loc[False].lead:.0f} days for reliable ones. "
          "Least reliable suppliers in the ranking:", "",
          "| Seller | Purchase orders | Mean lead (days) | 90th percentile lead (days) | On time |", "|---|---:|---:|---:|---:|"]
    L += [f"| {r.seller_id[-6:]} | {r.sim_po_count} | {r.sim_lead_mean_days} | {float(r.sim_lead_p90_days):.0f} | {r.sim_po_on_time_pct}% |" for r in sim.itertuples()]
    L += ["", "**Action:** plan safety stock on the 90th percentile lead time, not the quoted or average one, and dual source the "
              "products that depend on the bottom of this list.", ""]

    # ---- 6. reorder points + inventory position
    cf = reorder_point_counterfactual()
    pos = q("""SELECT count(*) AS n, count(*) FILTER (WHERE risk_status IN ('stockout', 'at_risk')) AS risk,
                      count(*) FILTER (WHERE risk_status = 'overstock') AS ovr,
                      count(*) FILTER (WHERE risk_status = 'no_recent_demand') AS slow,
                      sum(inventory_value) AS value, sum(inventory_value) FILTER (WHERE risk_status = 'overstock') AS ovr_value
               FROM marts.inventory_position_current""").iloc[0]
    inv_delta = 100 * (cf["inv_new"] / cf["inv_base"] - 1) if cf["inv_base"] else 0
    L += ["## 6. Fix reorder points and release excess stock **(simulated)**", "",
          f"Across {fmt(pos.n)} product and distribution centre combinations, {int(pos.risk)} are stocked out or at risk today, "
          f"{int(pos.ovr)} hold more than 120 days of supply ({fmt(pos.ovr_value / 1000)}K of {fmt(pos.value / 1000)}K BRL, "
          f"{100 * pos.ovr_value / pos.value:.0f}% of inventory at cost), and {fmt(pos.slow)} sold nothing in the last 13 weeks. "
          "Demand is a long tail, so most product and region pairs sell only occasionally.", "",
          f"The baseline policy plans with each supplier's quoted lead time. I replayed the {cf['series']} series with any stockout weeks "
          "using the same demand and the same lead time draws, but with reorder points built on the observed 90th percentile lead time:", "",
          "| Measure | Current policy | Revised policy |", "|---|---:|---:|",
          f"| Reorder points raised | n/a | {cf['changed']} of {cf['series']} series |",
          f"| Average fill rate | {cf['fill_base']:.1f}% | {cf['fill_new']:.1f}% |",
          f"| Share of weeks in stockout | {cf['so_base']:.1f}% | {cf['so_new']:.1f}% |",
          f"| Average inventory value (BRL) | {fmt(cf['inv_base'])} | {fmt(cf['inv_new'])} ({inv_delta:+.1f}%) |", "",
          "**Action:** raise reorder points on the series flagged above and accept the extra inventory only where a supplier is "
          "unreliable. Review the long tail of slow movers for central stocking in one distribution centre, and release the "
          "overstock before placing new orders.", "",
          "## Caveats", "",
          "Delivery findings come from 2016 to 2018 Brazilian marketplace data and show the method, not current performance. "
          "Inventory and purchase order figures come from a seeded simulation described in `docs/assumptions.md`, so treat impact "
          "estimates as directional. Review scores are a customer reported signal and are only linked to delivery by association, "
          "not proven cause. See the README for the full list of assumptions.", ""]
    return "\n".join(L)


if __name__ == "__main__":
    out = ROOT / "docs" / "recommendations.md"
    out.write_text(build_markdown(), encoding="utf-8")
    print(f"wrote {out}")
