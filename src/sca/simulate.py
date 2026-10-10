"""SIMULATED inventory and purchase-order layer (Olist has neither).

Demand is the REAL weekly Olist unit sales per product x regional DC. Everything else is synthetic:
a weekly-review (s, S) reorder policy runs against per-supplier lognormal lead times, so stockouts,
overstock and turnover emerge from the mechanics rather than from random noise. All output lives in
the `sim` schema and is flagged `is_simulated`. Assumptions are documented in docs/assumptions.md.

    python -m sca.simulate
"""
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import text

from . import db

SEED = 42
TOP_N_PRODUCTS = 500
Z_SERVICE = 1.28            # ~90% cycle service level used to set safety stock
COVER_WEEKS = 4             # order-up-to level S = s + 4 weeks of average demand
UNRELIABLE_SHARE = 0.15     # share of suppliers deliberately unreliable
COST_RATIO = 0.60           # unit cost proxy = 60% of average selling price


@dataclass
class SeriesResult:
    on_hand: np.ndarray
    on_order: np.ndarray
    sold: np.ndarray
    received: np.ndarray
    pos: list  # (week_idx, qty, lead_days) placed at end of week_idx


def simulate_series(demand: np.ndarray, reorder_point: int, order_up_to: int, draw_lead_days) -> SeriesResult:
    """Weekly loop: receive -> sell (lost sales if short) -> review -> order. Starts at S on hand.

    `draw_lead_days()` returns an integer >= 1. Inventory position = on_hand + on_order.
    """
    T = len(demand)
    on_hand = np.zeros(T, dtype=int)
    on_order = np.zeros(T, dtype=int)
    sold = np.zeros(T, dtype=int)
    received = np.zeros(T, dtype=int)
    pos: list = []
    stock = order_up_to
    pipeline: list[list[int]] = []  # [arrival_week, qty]
    for t in range(T):
        arrived = sum(q for w, q in pipeline if w <= t)
        pipeline = [p for p in pipeline if p[0] > t]
        stock += arrived
        received[t] = arrived
        sold[t] = min(stock, int(demand[t]))
        stock -= sold[t]
        outstanding = sum(q for _, q in pipeline)
        if stock + outstanding <= reorder_point:
            qty = order_up_to - (stock + outstanding)
            if qty > 0:
                lead = int(draw_lead_days())
                arrival = t + 1 + (lead - 1) // 7   # PO placed at end of week t (day 7); lands lead days later
                pipeline.append([arrival, qty])
                pos.append((t, qty, lead))
                outstanding += qty
        on_hand[t] = stock
        on_order[t] = outstanding
    return SeriesResult(on_hand, on_order, sold, received, pos)


def _supplier_profiles(supplier_keys: list[int], rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for sk in sorted(supplier_keys):
        unreliable = rng.random() < UNRELIABLE_SHARE
        quoted = int(rng.integers(5, 17))
        if unreliable:   # actually slower than quoted, and erratic
            median, sigma = quoted * rng.uniform(1.4, 2.0), rng.uniform(0.45, 0.7)
        else:
            median, sigma = quoted * rng.uniform(0.75, 0.95), rng.uniform(0.12, 0.28)
        rows.append({"supplier_key": sk, "quoted_lead_days": quoted, "actual_median_days": round(median, 2),
                     "lead_sigma": round(sigma, 3), "is_unreliable": unreliable, "is_simulated": True})
    return pd.DataFrame(rows)


def build(seed: int = SEED, top_n: int = TOP_N_PRODUCTS) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    eng = db.engine()
    lines = pd.read_sql(text("""
        SELECT f.product_key, f.warehouse_key, f.supplier_key, d.week_start, f.price
        FROM core.fact_order_line f JOIN core.dim_date d ON d.date_key = f.purchase_date_key
        WHERE f.order_status NOT IN ('canceled', 'unavailable')"""), eng, parse_dates=["week_start"])
    if lines.empty:
        raise RuntimeError("core.fact_order_line is empty; run the core SQL before simulate")

    top = lines.groupby("product_key").size().nlargest(top_n).index
    lines = lines[lines.product_key.isin(top)]
    # primary supplier = the supplier with most lines for the product (ties -> lowest key)
    primary = (lines.groupby(["product_key", "supplier_key"]).size().reset_index(name="n")
               .sort_values(["product_key", "n", "supplier_key"], ascending=[True, False, True])
               .drop_duplicates("product_key").set_index("product_key").supplier_key)
    unit_cost = (lines.groupby("product_key").price.mean() * COST_RATIO).round(2)

    weeks = pd.date_range(lines.week_start.min(), lines.week_start.max(), freq="7D")
    T = len(weeks)
    week_idx = {w: i for i, w in enumerate(weeks)}
    demand = (lines.assign(w=lines.week_start.map(week_idx))
              .groupby(["product_key", "warehouse_key", "w"]).size().rename("units").reset_index())

    profiles = _supplier_profiles(list(primary.unique()), rng)
    prof = profiles.set_index("supplier_key")

    snaps, pos_rows = [], []
    po_seq = 0
    for (pk, wk), g in demand.groupby(["product_key", "warehouse_key"], sort=True):
        d = np.zeros(T, dtype=int)
        d[g.w.values] = g.units.values
        sk = int(primary[pk])
        p = prof.loc[sk]
        L_weeks = p.quoted_lead_days / 7 + 1                      # lead time + 1 review period
        mu, sd = d.mean(), d.std()
        safety = max(1, math.ceil(Z_SERVICE * sd * math.sqrt(L_weeks)))
        s = max(1, math.ceil(mu * L_weeks) + safety)
        S = s + max(2, math.ceil(mu * COVER_WEEKS))

        def draw(p=p):   # lognormal around the supplier's ACTUAL median, not the quoted one
            return max(1, int(round(rng.lognormal(math.log(p.actual_median_days), p.lead_sigma))))

        r = simulate_series(d, s, S, draw)
        snaps.append(pd.DataFrame({
            "snapshot_date": weeks, "product_key": pk, "warehouse_key": wk, "supplier_key": sk,
            "on_hand": r.on_hand, "on_order": r.on_order, "reorder_point": s, "safety_stock": safety,
            "units_demand": d, "units_sold": r.sold, "units_received": r.received,
            "unit_cost": float(unit_cost[pk]), "is_simulated": True}))
        for t, qty, lead in r.pos:
            po_seq += 1
            order_date = weeks[t] + pd.Timedelta(days=6)
            pos_rows.append({
                "po_id": f"SIM-PO-{po_seq:07d}", "supplier_key": sk, "product_key": pk, "warehouse_key": wk,
                "order_date": order_date.date(),
                "expected_date": (order_date + pd.Timedelta(days=int(p.quoted_lead_days))).date(),
                "receipt_date": (order_date + pd.Timedelta(days=lead)).date(),
                "qty": qty, "unit_cost": float(unit_cost[pk]), "lead_days": lead, "is_simulated": True})

    return {"supplier_profile": profiles, "inventory_weekly": pd.concat(snaps, ignore_index=True),
            "purchase_order": pd.DataFrame(pos_rows)}


def run(seed: int = SEED, top_n: int = TOP_N_PRODUCTS) -> dict[str, int]:
    out = build(seed, top_n)
    eng = db.engine()
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS sim.supplier_profile, sim.inventory_weekly, sim.purchase_order CASCADE"))
    counts = {}
    for name, df in out.items():
        df.to_sql(name, eng, schema="sim", index=False, method="multi", chunksize=5000)
        counts[name] = len(df)
        db.log_step("simulate", f"sim.{name}", None, len(df))
        print(f"sim.{name}: {len(df):,} rows")
    return counts


if __name__ == "__main__":
    run()
